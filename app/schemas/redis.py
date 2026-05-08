from pydantic import BaseModel


class SetRequest(BaseModel):
    key: str
    value: str
