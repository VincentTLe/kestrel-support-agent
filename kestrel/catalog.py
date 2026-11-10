"""Catalog search. Code filters on price, style, height and stock; the model
only chooses among the results and words the answer."""

from .data import Product

MAX_NEAR_MISSES = 3


def search(
    catalog: list[Product],
    *,
    category: str | None = None,
    riding_style: str | None = None,
    max_price: float | None = None,
    rider_height_cm: float | None = None,
    name_or_sku: str | None = None,
) -> dict:
    """Return {"matches": [...], "near_misses": [...]}.

    A match meets every filter and can be bought now. Near misses are offered
    only when nothing matches, closest first, each with the reasons it missed.
    Category and name are hard filters: a helmet is never a near miss for a bike.
    """
    matches, near_misses = [], []
    for product in catalog:
        if category and product.category.casefold() != category.casefold():
            continue
        if name_or_sku and not _is_named(product, name_or_sku):
            continue

        why_not = []
        style_ok = not riding_style or _has_style(product, riding_style)
        if not style_ok:
            why_not.append(f"Riding style is {', '.join(product.riding_style)}, not {riding_style}.")
        if max_price is not None and product.price > max_price:
            why_not.append(f"${product.price - max_price:,.2f} over the ${max_price:,.2f} budget.")

        entry = _describe(product)
        stock_problem = _stock_problem(product, rider_height_cm, entry)
        if stock_problem:
            why_not.append(stock_problem)

        if why_not:
            entry["why_not"] = why_not
            near_misses.append((len(why_not), not style_ok, product.price, entry))
        else:
            matches.append(entry)

    if matches:
        return {"matches": matches, "near_misses": []}
    near_misses.sort(key=lambda scored: scored[:3])
    return {"matches": [], "near_misses": [entry for *_, entry in near_misses[:MAX_NEAR_MISSES]]}


def _is_named(product: Product, name_or_sku: str) -> bool:
    wanted = name_or_sku.strip().casefold()
    return wanted == product.sku.casefold() or wanted in product.name.casefold()


def _has_style(product: Product, riding_style: str) -> bool:
    styles = [style.casefold() for style in product.riding_style]
    return "all" in styles or riding_style.casefold() in styles


def _describe(product: Product) -> dict:
    entry = {
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "price_usd": product.price,
        "riding_style": product.riding_style,
        "description": product.description,
        "final_sale": product.final_sale,
    }
    if product.sizes is None:
        entry["in_stock"] = bool(product.in_stock)
    else:
        entry["sizes"] = [size.model_dump(exclude_none=True) for size in product.sizes]
    return entry


def _stock_problem(product: Product, rider_height_cm: float | None, entry: dict) -> str | None:
    """Why this product cannot be bought now (for this rider), or None if it can.

    When a height is given and the product has height ranges, records the sizes
    that fit in entry["fitting_sizes"]. Ranges are inclusive at both ends.
    """
    if product.sizes is None:
        return None if product.in_stock else "Out of stock."

    sized_by_height = any(size.rider_height_cm for size in product.sizes)
    if rider_height_cm is None or not sized_by_height:
        if any(size.in_stock for size in product.sizes):
            return None
        return "Out of stock in every size."

    fitting = [
        size
        for size in product.sizes
        if size.rider_height_cm and size.rider_height_cm[0] <= rider_height_cm <= size.rider_height_cm[1]
    ]
    entry["fitting_sizes"] = [{"size": size.size, "in_stock": size.in_stock} for size in fitting]
    if not fitting:
        return f"No size fits a {rider_height_cm:g} cm rider."
    if not any(size.in_stock for size in fitting):
        names = ", ".join(size.size for size in fitting)
        return f"The size that fits a {rider_height_cm:g} cm rider ({names}) is out of stock."
    return None
