"""
Database Schemas for TheRawKing

Each Pydantic model represents a MongoDB collection. Collection name is the lowercase of the class name.
"""

from pydantic import BaseModel, Field
from typing import Optional, List

class Product(BaseModel):
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    currency: str = Field("usd", description="ISO currency code")
    images: List[str] = Field(default_factory=list, description="Image URLs")
    sizes: List[str] = Field(default_factory=lambda: ["S","M","L","XL"], description="Available sizes")
    in_stock: bool = Field(True, description="In stock flag")
    featured: bool = Field(False, description="Featured on home page")
    color: Optional[str] = Field(None, description="Color variant")
    tag: Optional[str] = Field(None, description="Tag label like 'New' or 'Limited'")

class OrderItem(BaseModel):
    product_id: str = Field(..., description="Referenced product id")
    title: str
    price: float
    quantity: int = Field(ge=1, default=1)
    size: Optional[str] = None

class Order(BaseModel):
    email: str
    items: List[OrderItem]
    total: float
    currency: str = "usd"
    payment_status: str = Field("pending", description="pending | paid | failed | refunded")
    stripe_payment_intent_id: Optional[str] = None
    stripe_session_id: Optional[str] = None
