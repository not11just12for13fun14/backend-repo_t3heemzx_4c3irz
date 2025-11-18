import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents
from schemas import Product, Order, OrderItem

# Stripe setup
import stripe
from bson import ObjectId
from datetime import datetime

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
BACKEND_ORIGIN = os.getenv("BACKEND_ORIGIN", "http://localhost:8000")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(title="TheRawKing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "TheRawKing backend running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": [],
        "stripe": "✅ Configured" if STRIPE_SECRET_KEY else "⚠️ Missing STRIPE_SECRET_KEY",
    }

    try:
        if db is not None:
            response["database"] = "✅ Connected"
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["connection_status"] = "Connected"
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"

    return response


# Seed products
MOON_PRODUCTS = [
    {
        "title": "Lunar Phase Tee",
        "description": "Premium cotton tee featuring the moon phases in subtle reflective ink.",
        "price": 32.0,
        "currency": "usd",
        "images": [
            "https://images.unsplash.com/photo-1520975693411-b2f4a45f66f6?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L", "XL"],
        "in_stock": True,
        "featured": True,
        "color": "Black",
        "tag": "New",
    },
    {
        "title": "Moonrise Oversized Tee",
        "description": "Oversized fit with gradient moonrise graphic – ultra-soft and breathable.",
        "price": 38.0,
        "currency": "usd",
        "images": [
            "https://images.unsplash.com/photo-1520975655913-61e5d1e0b5f4?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L", "XL"],
        "in_stock": True,
        "featured": True,
        "color": "Midnight Blue",
        "tag": "Limited",
    },
    {
        "title": "Eclipse Minimal Tee",
        "description": "Clean eclipse ring chest print. Minimal. Bold. Cosmic.",
        "price": 29.0,
        "currency": "usd",
        "images": [
            "https://images.unsplash.com/photo-1491553895911-0055eca6402d?q=80&w=1200&auto=format&fit=crop",
        ],
        "sizes": ["S", "M", "L", "XL"],
        "in_stock": True,
        "featured": False,
        "color": "Charcoal",
        "tag": None,
    },
]


def seed_products_if_empty():
    if db is None:
        return
    existing = list(db["product"].find({}).limit(1))
    if not existing:
        for p in MOON_PRODUCTS:
            try:
                create_document("product", Product(**p))
            except Exception:
                pass


class CreateCheckoutItem(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, default=1)
    size: Optional[str] = None


class CreateCheckoutRequest(BaseModel):
    email: Optional[str] = None
    items: List[CreateCheckoutItem]


@app.get("/api/products")
def list_products():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    seed_products_if_empty()
    docs = get_documents("product")
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return {"products": docs}


@app.post("/api/create-checkout-session")
def create_checkout_session(payload: CreateCheckoutRequest, request: Request):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured. Set STRIPE_SECRET_KEY.")

    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    line_items = []
    order_items: List[OrderItem] = []

    for item in payload.items:
        try:
            prod = db["product"].find_one({"_id": ObjectId(item.product_id)})
        except Exception:
            prod = None
        if not prod:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")

        price_in_cents = int(float(prod.get("price", 0)) * 100)
        title = prod.get("title", "Item")
        currency = prod.get("currency", "usd")
        image = (prod.get("images") or [None])[0]

        line_items.append(
            {
                "price_data": {
                    "currency": currency,
                    "product_data": {
                        "name": title,
                        "images": [image] if image else [],
                    },
                    "unit_amount": price_in_cents,
                },
                "quantity": item.quantity,
            }
        )

        order_items.append(
            OrderItem(
                product_id=str(prod.get("_id")),
                title=title,
                price=float(prod.get("price", 0)),
                quantity=item.quantity,
                size=item.size,
            )
        )

    success_url = f"{FRONTEND_ORIGIN}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{FRONTEND_ORIGIN}/checkout/cancel"

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=line_items,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=payload.email if payload.email else None,
            metadata={"brand": "TheRawKing"},
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    total = sum(oi.price * oi.quantity for oi in order_items)
    order = Order(
        email=payload.email or "",
        items=order_items,
        total=total,
        currency="usd",
        payment_status="pending",
        stripe_session_id=session.id,
        stripe_payment_intent_id=session.payment_intent if isinstance(session.payment_intent, str) else None,
    )

    try:
        create_document("order", order)
    except Exception:
        pass

    return {"url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not STRIPE_WEBHOOK_SECRET:
        return {"received": True, "warning": "No webhook secret set"}

    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id")
        if db is not None and session_id:
            db["order"].update_one(
                {"stripe_session_id": session_id},
                {"$set": {"payment_status": "paid", "updated_at": datetime.utcnow()}},
            )

    if event["type"] == "payment_intent.payment_failed":
        pi = event["data"]["object"]
        pid = pi.get("id")
        if db is not None and pid:
            db["order"].update_many(
                {"stripe_payment_intent_id": pid},
                {"$set": {"payment_status": "failed", "updated_at": datetime.utcnow()}},
            )

    return {"received": True}


@app.get("/api/order/by-session/{session_id}")
def order_by_session(session_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    doc = db["order"].find_one({"stripe_session_id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    doc["id"] = str(doc.pop("_id"))
    return doc


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
