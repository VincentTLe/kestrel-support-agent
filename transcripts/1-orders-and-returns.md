Clock: 2026-12-01 09:30 PM Eastern

**Customer:** Hi! Where's my order? KC-10433, marcus.reed@example.com

    [tool] lookup_order({"email": "marcus.reed@example.com", "order_number": "KC-10433"})
**Kestrel:**

```text
Good news, Marcus: your order is on its way.

Here's the latest on your order:
KC-10433

It shipped on November 28 and hasn't been marked delivered yet. Track it here:
https://track.kestrelcycles.example/1Z84KC0002

Keep the rubber side down!
```

**Customer:** Thanks. Also, my email is ava.chen@example.com and I'd like to return the helmet from order KC-10421.

    [tool] lookup_order({"email": "ava.chen@example.com", "order_number": "KC-10421"})
**Kestrel:**

```text
Good news, Ava: the helmet on your order is eligible for return.

Ridgeline Trail Helmet (size M) from your order:
KC-10421

It was delivered on November 20, and the 30-day return window is open through December 20, 2026.

You can start the return here:
https://kestrelcycles.example/returns

Smooth roads ahead!
```

**Customer:** And the jacket from KC-10440? Same email.

    [tool] lookup_order({"email": "ava.chen@example.com", "order_number": "KC-10440"})
**Kestrel:**

```text
The Velo Rain Shell Jacket (size S) from order KC-10440 is not eligible for return.
KC-10440

It was delivered on October 15, and the 30-day return window ended on November 14, 2026.
```
