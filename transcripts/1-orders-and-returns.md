Clock: 2026-12-01 09:30 PM Eastern

**Customer:** Hi! Where's my order? KC-10433, marcus.reed@example.com

    [tool] lookup_order({"email": "marcus.reed@example.com", "order_number": "KC-10433"})
**Kestrel:**

```text
Hi Marcus, good news: your order is on the road.

Here's the latest on your order:
KC-10433

It shipped on November 28 and hasn't been marked delivered yet. Track it here:
https://track.kestrelcycles.example/1Z84KC0002

Your order includes one Switchback 29 Hardtail in size L. Smooth roads ahead!
```

**Customer:** Thanks. Also, my email is ava.chen@example.com and I'd like to return the helmet from order KC-10421.

    [tool] lookup_order({"email": "ava.chen@example.com", "order_number": "KC-10421"})
**Kestrel:**

```text
Good news, Ava: the Ridgeline Trail Helmet (size M) from your order is eligible for return. The return window is open through December 20, 2026.

You can start the return here:
https://kestrelcycles.example/returns

I can't process the return myself, so you'll start it at that link.
```

**Customer:** And the jacket from KC-10440? Same email.

    [tool] lookup_order({"email": "ava.chen@example.com", "order_number": "KC-10440"})
**Kestrel:**

```text
Ava, I checked this order:
KC-10440

The Velo Rain Shell Jacket (size S) isn't eligible for return. It was delivered on October 15, 2026, and the 30-day return window ended on November 14, 2026.
```
