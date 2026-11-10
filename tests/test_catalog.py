from kestrel.catalog import search


def skus(products):
    return [p["sku"] for p in products]


def by_sku(products, sku):
    return next(p for p in products if p["sku"] == sku)


def test_no_filters_returns_every_product_that_is_in_stock(catalog):
    result = search(catalog)
    assert len(result["matches"]) == 13
    assert "KCB-302" not in skus(result["matches"])  # Metro E-Assist: all sizes out


def test_budget_filter(catalog):
    result = search(catalog, category="Bikes", max_price=1000)
    assert sorted(skus(result["matches"])) == ["KCB-301", "KCB-501"]


def test_riding_style_filter_excludes_out_of_stock_bike(catalog):
    result = search(catalog, category="Bikes", riding_style="commuting")
    assert skus(result["matches"]) == ["KCB-301"]


def test_products_tagged_all_match_any_riding_style(catalog):
    result = search(catalog, category="Accessories", riding_style="mountain")
    assert sorted(skus(result["matches"])) == ["KCA-310", "KCA-320"]


def test_height_on_a_shared_boundary_fits_both_sizes(catalog):
    result = search(catalog, name_or_sku="KCB-101", rider_height_cm=167)
    fitting = by_sku(result["matches"], "KCB-101")["fitting_sizes"]
    assert [s["size"] for s in fitting] == ["S", "M"]


def test_fitting_size_out_of_stock_is_a_near_miss_not_a_match(catalog):
    result = search(catalog, category="Bikes", riding_style="mountain", rider_height_cm=190)
    assert result["matches"] == []
    near = by_sku(result["near_misses"], "KCB-101")
    assert any("XL" in why and "out of stock" in why for why in near["why_not"])


def test_rider_taller_than_every_size_is_a_near_miss(catalog):
    result = search(catalog, name_or_sku="KCB-102", rider_height_cm=195)
    assert result["matches"] == []
    assert any("no size" in why.lower() for why in result["near_misses"][0]["why_not"])


def test_out_of_stock_bike_is_reported_as_a_near_miss_with_the_reason(catalog):
    result = search(catalog, category="Bikes", riding_style="e-bike")
    assert result["matches"] == []
    first = result["near_misses"][0]
    assert first["sku"] == "KCB-302"
    assert any("out of stock" in why.lower() for why in first["why_not"])


def test_over_budget_near_miss_says_by_how_much(catalog):
    result = search(catalog, category="Bikes", riding_style="mountain", max_price=1000)
    assert result["matches"] == []
    near = by_sku(result["near_misses"], "KCB-101")
    assert any("450" in why for why in near["why_not"])


def test_near_misses_are_capped_and_closest_first(catalog):
    result = search(catalog, category="Bikes", riding_style="mountain", max_price=1000)
    assert len(result["near_misses"]) <= 3
    counts = [len(p["why_not"]) for p in result["near_misses"]]
    assert counts == sorted(counts)


def test_near_misses_are_only_offered_when_nothing_matches(catalog):
    result = search(catalog, category="Bikes", riding_style="commuting")
    assert result["near_misses"] == []


def test_partly_stocked_product_matches_and_shows_which_size_is_out(catalog):
    result = search(catalog, category="Helmets", riding_style="commuting")
    helmet = by_sku(result["matches"], "KCH-220")
    stock = {s["size"]: s["in_stock"] for s in helmet["sizes"]}
    assert stock == {"M": True, "L": False}


def test_height_is_ignored_for_products_without_height_ranges(catalog):
    result = search(catalog, category="Helmets", rider_height_cm=190)
    assert len(result["matches"]) == 2


def test_lookup_by_name_is_case_insensitive_substring(catalog):
    result = search(catalog, name_or_sku="tempo")
    assert skus(result["matches"]) == ["KCB-401"]


def test_unknown_product_name_returns_nothing(catalog):
    result = search(catalog, name_or_sku="unicycle")
    assert result == {"matches": [], "near_misses": []}


def test_category_is_a_hard_filter_even_for_near_misses(catalog):
    result = search(catalog, category="Bikes", max_price=100)
    assert result["matches"] == []
    assert all(p["category"] == "Bikes" for p in result["near_misses"])
