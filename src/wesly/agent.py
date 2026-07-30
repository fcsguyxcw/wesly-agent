from collections.abc import Callable, Iterator, Sequence
from typing import Literal

from wesly.context import (
    ContextBuilder,
    ContextLimitError,
    DirectAnswerContextBuilder,
)
from wesly.evidence import extract_file_citations
from wesly.events import (
    AgentEvent,
    ApprovalDecided,
    ApprovalRequested,
    FileDiffProposed,
    ModelCompleted,
    ModelStarted,
    RunCompleted,
    RunFailed,
    ToolCompleted,
    ToolStarted,
)
from wesly.model import Message, ModelClient, ModelProviderError, Usage
from wesly.permissions import (
    ApprovalDecisionReason,
    ApprovalProvider,
    ApprovalTimedOutError,
)
from wesly.tools import ToolRegistry


class Agent:
    def __init__(
        self,
        model_client: ModelClient,
        context_builder: ContextBuilder | None = None,
        tool_registry: ToolRegistry | None = None,
        approval_provider: ApprovalProvider | None = None,
        approval_audit: Callable[[ApprovalRequested | ApprovalDecided], None]
        | None = None,
        max_model_turns: int = 12,
        max_tool_calls: int = 30,
    ) -> None:
        self._model_client = model_client
        self._context_builder = context_builder or DirectAnswerContextBuilder()
        self._tool_registry = tool_registry
        self._approval_provider = approval_provider
        self._approval_audit = approval_audit
        self._max_model_turns = max_model_turns
        self._max_tool_calls = max_tool_calls

    def run(
        self,
        task: str,
        history: Sequence[Message] = (),
        *,
        verification_state: Literal["clean", "required", "failed"] = "clean",
    ) -> Iterator[AgentEvent]:
        current_history = list(history)
        tool_calls = 0
        usage = Usage(input_tokens=0, output_tokens=0)
        observed_evidence: set[str] = set()

        for turn in range(1, self._max_model_turns + 1):
            try:
                request = self._context_builder.build(task, current_history)
            except ContextLimitError as error:
                yield RunFailed(
                    stop_reason="context_limit",
                    message=str(error),
                    model_turns=turn - 1,
                    tool_calls=tool_calls,
                )
                return
            yield ModelStarted(turn=turn)
            try:
                model_turn = self._model_client.complete(request)
            except KeyboardInterrupt:
                yield RunFailed(
                    stop_reason="interrupted",
                    message="任务已由用户中断",
                    model_turns=turn - 1,
                    tool_calls=tool_calls,
                )
                return
            except ModelProviderError as error:
                yield RunFailed(
                    stop_reason="provider_error",
                    message=str(error),
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return

            usage = Usage(
                input_tokens=usage.input_tokens + model_turn.usage.input_tokens,
                output_tokens=usage.output_tokens + model_turn.usage.output_tokens,
            )
            assistant_message = Message(
                role="assistant",
                content=model_turn.content,
                tool_calls=model_turn.tool_calls,
            )
            yield ModelCompleted(
                turn=turn,
                finish_reason=model_turn.finish_reason,
                usage=model_turn.usage,
                message=assistant_message,
            )
            current_history.append(assistant_message)

            if model_turn.tool_calls:
                if self._tool_registry is None:
                    yield RunFailed(
                        stop_reason="internal_error",
                        message="Agent 尚未配置工具注册表",
                        model_turns=turn,
                        tool_calls=tool_calls,
                    )
                    return
                for call in model_turn.tool_calls:
                    if tool_calls >= self._max_tool_calls:
                        yield RunFailed(
                            stop_reason="tool_limit",
                            message="已达到工具调用上限",
                            model_turns=turn,
                            tool_calls=tool_calls,
                        )
                        return
                    target = self._tool_registry.describe_target(call)
                    yield ToolStarted(
                        call_id=call.id,
                        tool_name=call.name,
                        target=target,
                    )
                    try:
                        preview = self._tool_registry.preview(call)
                        if preview is not None:
                            yield FileDiffProposed(
                                call_id=call.id,
                                path=preview.path,
                                diff=preview.diff,
                            )
                        try:
                            prepared_operation = self._tool_registry.prepare_operation(call)
                        except Exception:
                            yield RunFailed(
                                stop_reason="permission_error",
                                message="权限策略评估失败；操作未执行",
                                model_turns=turn,
                                tool_calls=tool_calls,
                            )
                            return
                        approved_operation = None
                        if prepared_operation is not None:
                            requested_event = ApprovalRequested(
                                call_id=call.id,
                                fingerprint=prepared_operation.fingerprint,
                                operation=prepared_operation.operation,
                                parameters=prepared_operation.parameters,
                                resolved_targets=prepared_operation.resolved_targets,
                                reason=prepared_operation.reason,
                                impact_scope=prepared_operation.impact_scope,
                                workspace=prepared_operation.workspace,
                                sensitivity=prepared_operation.sensitivity,
                            )
                            if not self._record_approval_event(requested_event):
                                self._tool_registry.revoke_operation(prepared_operation)
                                yield RunFailed(
                                    stop_reason="permission_error",
                                    message="审批审计失败；操作未执行",
                                    model_turns=turn,
                                    tool_calls=tool_calls,
                                )
                                return
                            yield requested_event
                            decision_reason: ApprovalDecisionReason = "user"
                            interrupted = False
                            try:
                                decision = (
                                    self._approval_provider.decide(prepared_operation)
                                    if self._approval_provider is not None
                                    else "deny"
                                )
                            except ApprovalTimedOutError:
                                decision = "deny"
                                decision_reason = "timeout"
                            except KeyboardInterrupt:
                                decision = "deny"
                                decision_reason = "interrupted"
                                interrupted = True
                            except Exception:
                                decision = "deny"
                                decision_reason = "approval_error"
                            decided_event = ApprovalDecided(
                                call_id=call.id,
                                fingerprint=prepared_operation.fingerprint,
                                decision=decision,
                                reason=decision_reason,
                            )
                            if not self._record_approval_event(decided_event):
                                self._tool_registry.revoke_operation(prepared_operation)
                                yield RunFailed(
                                    stop_reason="permission_error",
                                    message="审批审计失败；操作未执行",
                                    model_turns=turn,
                                    tool_calls=tool_calls,
                                )
                                return
                            yield decided_event
                            if interrupted:
                                self._tool_registry.revoke_operation(prepared_operation)
                                yield RunFailed(
                                    stop_reason="interrupted",
                                    message="任务已由用户中断",
                                    model_turns=turn,
                                    tool_calls=tool_calls,
                                )
                                return
                            if decision == "allow_once":
                                approved_operation = prepared_operation
                        result = self._tool_registry.execute(
                            call,
                            preview=preview,
                            approved_operation=approved_operation,
                        )
                    except KeyboardInterrupt:
                        yield RunFailed(
                            stop_reason="interrupted",
                            message="任务已由用户中断",
                            model_turns=turn,
                            tool_calls=tool_calls,
                        )
                        return
                    tool_calls += 1
                    observed_evidence.update(result.evidence_paths)
                    if result.tool_name == "run_command" and result.command_purpose == "verify":
                        if result.status == "error":
                            verification_state = "failed"
                        elif result.changed_paths:
                            verification_state = "required"
                        else:
                            verification_state = "clean"
                    elif result.changed_paths or (
                        result.tool_name == "run_command"
                        and result.status == "success"
                        and result.command_purpose in {"build", "modify"}
                    ):
                        verification_state = "required"
                    tool_message = Message(
                        role="tool",
                        content=result.content,
                        tool_call_id=result.call_id,
                    )
                    yield ToolCompleted(
                        call_id=result.call_id,
                        tool_name=result.tool_name,
                        status=result.status,
                        target=result.target,
                        error_code=result.error_code,
                        changed_paths=result.changed_paths,
                        exit_code=result.exit_code,
                        timed_out=result.timed_out,
                        output_truncated=result.output_truncated,
                        command_purpose=result.command_purpose,
                        change_tracking_complete=result.change_tracking_complete,
                        message=tool_message,
                    )
                    current_history.append(tool_message)
                continue

            if not model_turn.content:
                yield RunFailed(
                    stop_reason="provider_error",
                    message="模型响应不包含答案",
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return
            if verification_state != "clean":
                stop_reason = (
                    "verification_failed"
                    if verification_state == "failed"
                    else "verification_required"
                )
                message = (
                    "最近一次明确验证失败；任务不能报告完成"
                    if verification_state == "failed"
                    else "最后一次工作区变更之后缺少明确且成功的验证"
                )
                yield RunFailed(
                    stop_reason=stop_reason,
                    message=message,
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return
            citations = extract_file_citations(model_turn.content)
            missing_evidence = tuple(
                path for path in citations if path not in observed_evidence
            )
            if missing_evidence:
                yield RunFailed(
                    stop_reason="evidence_error",
                    message=(
                        "模型引用了本次运行未观察的文件: "
                        + ", ".join(missing_evidence)
                    ),
                    model_turns=turn,
                    tool_calls=tool_calls,
                )
                return
            yield RunCompleted(
                answer=model_turn.content,
                model_turns=turn,
                tool_calls=tool_calls,
                usage=usage,
                evidence_paths=citations,
            )
            return

        yield RunFailed(
            stop_reason="turn_limit",
            message="已达到模型轮次上限",
            model_turns=self._max_model_turns,
            tool_calls=tool_calls,
        )

    def _record_approval_event(
        self,
        event: ApprovalRequested | ApprovalDecided,
    ) -> bool:
        if self._approval_audit is None:
            return True
        try:
            self._approval_audit(event)
        except Exception:
            return False
        return True
