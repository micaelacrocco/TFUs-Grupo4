from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    #user_id: int = Field(gt=0) --> Viene por cabecera
    asset_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    total_price: float = Field(gt=0, allow_inf_nan=False)
