Clock: 2026-12-01 09:30 PM Eastern

**Customer:** Hi, where's my order KC-10432? My email is marcus.reed@example.com

    [tool] lookup_order({"email": "marcus.reed@example.com", "order_number": "KC-10432"})
**Kestrel:**

```text
I couldn't find an order matching that email and order number together. Let's get you sorted.

Did you place this order with a different email address? If not, what is the full name on the order, so I can search for a close match to the order number?
```

**Customer:** It's Marcus Reed

    [tool] recover_order({"email": "marcus.reed@example.com", "customer_name": "Marcus Reed", "order_number": "KC-10432"})
**Kestrel:**

```text
I found your order. It's KC-10433, one digit off from the number you gave me.

Here's the latest on your order:
KC-10433

The Switchback 29 Hardtail (size L) shipped on November 28 and is on its way. Track it here:
https://track.kestrelcycles.example/1Z84KC0002

Once it's delivered, it can be returned within 30 days of delivery.
```

**Customer:** Great. I'm 183 cm, will that bike fit me?

    [tool] search_products({"name_or_sku": "Switchback 29 Hardtail", "category": "Bikes", "rider_height": 183, "height_unit": "cm"})
**Kestrel:**

```text
Good news, the Switchback 29 Hardtail fits you at 183 cm. Size L is the one for your height, and it's in stock. Your order is already in size L, so you're set.
```
