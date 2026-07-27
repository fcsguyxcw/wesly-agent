# Reviewable final answer

The live answer established that `URLSafeTimedSerializer` combines `URLSafeSerializerMixin` with `TimedSerializer`. Evidence: [[src/itsdangerous/url_safe.py]] and [[src/itsdangerous/timed.py]].

On `dumps`, `Serializer.dumps` calls the mixin's payload encoder, which serializes JSON, conditionally compresses it, applies URL-safe base64, and then signs through the timed serializer's `TimestampSigner`. Evidence: [[src/itsdangerous/serializer.py]], [[src/itsdangerous/url_safe.py]], [[src/itsdangerous/encoding.py]], and [[src/itsdangerous/signer.py]].

On `loads`, `TimedSerializer.loads` delegates signature removal to `TimestampSigner.unsign`; that method verifies the signature, decodes the timestamp, and raises `SignatureExpired` when the computed age exceeds `max_age` or is negative. The mixin then base64-decodes, optionally decompresses, and delegates JSON deserialization. Evidence: [[src/itsdangerous/timed.py]], [[src/itsdangerous/url_safe.py]], and [[src/itsdangerous/serializer.py]].

The original live answer contained a longer method-by-method walkthrough and the six recorded evidence paths. No target file changed.
