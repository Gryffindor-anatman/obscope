from pydantic import BaseModel


class AppCredential(BaseModel):
    app_id: str
    app_key: str
    app_secret: str
    description: str
    is_active: bool
