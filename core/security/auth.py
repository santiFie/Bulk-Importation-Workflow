from langgraph_sdk import Auth

auth = Auth()


VALID_TOKENS = {
    "secure_token_123": {"id": "user_123", "name": "John Doe", "role": "admin"},
    "secure_token_456": {"id": "user_456", "name": "Jane Smith", "role": "user"}
}

def _get_header(headers: dict[bytes, bytes] | None, name: str) -> str | None:
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        key_text = key.decode("latin-1") if isinstance(key, bytes) else str(key)
        if key_text.lower() == target:
            return value.decode("latin-1") if isinstance(value, bytes) else str(value)

    return None


@auth.authenticate
async def get_current_user(headers: dict[bytes, bytes] | None = None):
    """Check if user is valid."""

    token = _get_header(headers, "x-api-key")
    if not token:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing x-api-key header")

    if token not in VALID_TOKENS:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")

    # Return user information if it is valid.
    user_data = VALID_TOKENS[token]

    return {
        "identity": user_data["id"],
        "role": [user_data["role"]],
    }


@auth.on
async def add_owner(
    ctx: Auth.types.AuthContext,
    value: dict,
):
    """Make resources private for their creator"""
    # Add owner when creating a resource
    filters = {"owner": ctx.user["identity"]}
    metadata = value.setdefault("metadata", {})
    metadata.update(filters)

    # Only let users see their own resources
    return filters
