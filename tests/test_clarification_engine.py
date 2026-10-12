from datetime import date

import pandas as pd

from app.calc.metrics import Rules
from app.context import UserContext
from app.llm.schema import Filters, Intent, TimeWindow
from app.llm.client import Usage
from app.orchestrator import Orchestrator, _merge_scenario_clarification_reply, _usage_or_zero, parse_clarification_period
from app.semantic.resolver import EntityResolver
from app.session import SessionMemory


def _intent():
    return Intent(
        capability="inventory_visibility",
        metrics=["quantity"],
        filters=Filters(categories=["Laptops"], cities=["Chennai"]),
        time=TimeWindow(),
    )


def _resolver():
    products = pd.DataFrame([
        {"product_id": 1, "sku": "A", "product_name": "Laptop A", "brand": "Dell", "category": "Laptops", "model": "A", "subcategory": None},
    ])
    stores = pd.DataFrame([
        {"store_id": 10, "store_name": "Central", "city": "Chennai", "region": "South"},
        {"store_id": 11, "store_name": "Velachery", "city": "Chennai", "region": "South"},
        {"store_id": 20, "store_name": "Delhi One", "city": "Delhi", "region": "North"},
    ])
    return EntityResolver(products, stores)


def _orchestrator():
    return Orchestrator(
        llm=object(), queries=object(), resolver=_resolver(), rules=Rules(),
        session_memory=SessionMemory(), dispatch_fn=lambda **kwargs: None,
    )


def test_city_options_are_limited_to_authorized_stores():
    resolver = _resolver()
    options = resolver.city_store_options("Chennai", authorized_store_ids=(11,))
    assert [item["store_name"] for item in options] == ["Velachery"]


def test_store_level_capability_offers_only_authorized_city_options():
    orchestrator = _orchestrator()
    intent = _intent().model_copy(update={
        "filters": Filters(categories=["Laptops"]),
        "capability": "excess_slow_moving",
        "analysis_scope": "store",
    })
    response = orchestrator._location_clarification(
        intent=intent,
        resolution=_resolver().resolve(Filters(categories=["Laptops"])),
        session_id="city-choice",
        previous_state=orchestrator.sessions.get("city-choice"),
        user_context=UserContext(
            persona="Leadership", authorized_store_ids=(11, 20),
        ),
        usage=Usage(calls=0),
    )

    assert response.status == "clarification"
    assert response.clarification_kind == "location"
    assert {option["label"] for option in response.clarification_options} == {"Chennai", "Delhi"}


def test_city_selection_prompts_for_authorized_store_and_preserves_scope():
    orchestrator = _orchestrator()
    intent = _intent().model_copy(update={
        "capability": "excess_slow_moving",
        "analysis_scope": "store",
    })
    city_prompt = orchestrator._clarification_response(
        session_id="store-choice",
        intent=intent,
        kind="location",
        prompt="Which city?",
        options=[{"label": "Chennai", "value": "Chennai"}],
        field="cities",
    )
    selected, matched, error = orchestrator._resume_pending(
        city_prompt.clarification_options[0]["token"],
        orchestrator.sessions.pending("store-choice"),
    )
    assert matched and error is None
    assert selected.filters.categories == ["Laptops"]
    assert selected.filters.cities == ["Chennai"]

    resolver = _resolver()
    response = orchestrator._location_clarification(
        intent=selected,
        resolution=resolver.resolve(selected.filters),
        session_id="store-choice",
        previous_state=orchestrator.sessions.get("store-choice"),
        user_context=UserContext(
            persona="Leadership", authorized_store_ids=(11,),
        ),
        usage=Usage(calls=0),
    )

    assert response.status == "clarification"
    assert response.clarification_kind == "location_store"
    assert [option["label"] for option in response.clarification_options] == ["Velachery"]
    assert response.intent.filters.categories == ["Laptops"]
    assert response.intent.filters.cities == ["Chennai"]


def test_city_store_clarification_preserves_original_analytical_request():
    orchestrator = _orchestrator()
    resolver = _resolver()
    original_question = "Which slow-moving laptop products should I review?"
    intent = Intent(
        capability="excess_slow_moving",
        inventory_classification="slow_moving_candidate",
        analysis_scope="store",
        metrics=["sales_units"],
        filters=Filters(categories=["Laptops"]),
        time=TimeWindow(kind="last_n_days", n=30),
        question_kind="ranking",
    )
    city_response = orchestrator._clarification_response(
        session_id="full-city-store",
        intent=intent,
        kind="location",
        prompt="Which city?",
        options=[{"label": "Chennai", "value": "Chennai"}],
        field="cities",
        original_question=original_question,
    )
    pending_city = orchestrator.sessions.pending("full-city-store")
    selected_city, matched, error = orchestrator._resume_pending(
        city_response.clarification_options[0]["token"],
        pending_city,
    )
    assert matched and error is None

    store_response = orchestrator._location_clarification(
        intent=selected_city,
        resolution=resolver.resolve(selected_city.filters),
        session_id="full-city-store",
        previous_state=orchestrator.sessions.get("full-city-store"),
        user_context=UserContext(
            persona="Leadership", authorized_store_ids=(11,),
        ),
        usage=Usage(calls=0),
        original_question=pending_city["original_question"],
    )
    pending_store = orchestrator.sessions.pending("full-city-store")
    selected_store, matched, error = orchestrator._resume_pending(
        store_response.clarification_options[0]["token"],
        pending_store,
    )

    assert store_response.status == "clarification"
    assert [option["label"] for option in store_response.clarification_options] == ["Velachery"]
    assert pending_store["original_question"] == original_question
    assert matched and error is None
    assert selected_store.capability == "excess_slow_moving"
    assert selected_store.inventory_classification == "slow_moving_candidate"
    assert selected_store.metrics == ["sales_units"]
    assert selected_store.filters.categories == ["Laptops"]
    assert selected_store.filters.cities == ["Chennai"]
    assert selected_store.filters.stores == ["Velachery"]
    assert selected_store.time.n == 30


def test_city_and_region_filters_still_prompt_for_store_within_resolved_scope():
    orchestrator = _orchestrator()
    resolver = _resolver()
    filters = Filters(categories=["Laptops"], cities=["Chennai"], regions=["South"])
    intent = Intent(
        capability="excess_slow_moving",
        inventory_classification="slow_moving_candidate",
        analysis_scope="store",
        metrics=["sales_units"],
        filters=filters,
    )
    response = orchestrator._location_clarification(
        intent=intent,
        resolution=resolver.resolve(filters),
        session_id="city-region-choice",
        previous_state=orchestrator.sessions.get("city-region-choice"),
        user_context=UserContext(
            persona="Leadership", authorized_store_ids=(10, 11),
        ),
        usage=Usage(calls=0),
    )

    assert response.status == "clarification"
    assert response.clarification_kind == "location_store"
    assert {option["label"] for option in response.clarification_options} == {
        "Central", "Velachery",
    }


def test_uniquely_resolved_explicit_store_executes_without_clarification():
    orchestrator = _orchestrator()
    resolver = _resolver()
    filters = Filters(categories=["Laptops"], stores=["Central"])
    intent = Intent(
        capability="excess_slow_moving",
        inventory_classification="slow_moving_candidate",
        analysis_scope="store",
        metrics=["sales_units"],
        filters=filters,
    )

    assert orchestrator._location_clarification(
        intent=intent,
        resolution=resolver.resolve(filters),
        session_id="explicit-store",
        previous_state=orchestrator.sessions.get("explicit-store"),
        user_context=UserContext(
            persona="Leadership", authorized_store_ids=(10,),
        ),
        usage=Usage(calls=0),
    ) is None


def test_organization_scope_and_grouped_rankings_do_not_prompt_for_store():
    orchestrator = _orchestrator()
    resolution = _resolver().resolve(Filters())
    previous = orchestrator.sessions.get("organization")
    user_context = UserContext(
        persona="Leadership", authorized_store_ids=(10, 11, 20),
    )
    org_intent = _intent().model_copy(update={
        "capability": "excess_slow_moving",
        "analysis_scope": "organization",
    })
    assert orchestrator._location_clarification(
        intent=org_intent,
        resolution=resolution,
        session_id="organization",
        previous_state=previous,
        user_context=user_context,
        usage=Usage(calls=0),
    ) is None

    category_ranking = org_intent.model_copy(update={
        "analysis_scope": "store",
        "group_by": "category",
        "question_kind": "ranking",
    })
    assert orchestrator._location_clarification(
        intent=category_ranking,
        resolution=resolution,
        session_id="organization",
        previous_state=previous,
        user_context=user_context,
        usage=Usage(calls=0),
    ) is None


def test_pending_store_choice_merges_only_the_selected_scope():
    orchestrator = _orchestrator()
    response = orchestrator._clarification_response(
        session_id="s1", intent=_intent(), kind="location_store",
        prompt="Which store?", options=[{"label": "Central", "values": ["Central"]}],
    )
    token = response.clarification_options[0]["token"]
    resumed, matched, error = orchestrator._resume_pending(
        token, orchestrator.sessions.pending("s1")
    )
    assert matched and error is None
    assert resumed.filters.categories == ["Laptops"]
    assert resumed.filters.cities == ["Chennai"]
    assert resumed.filters.stores == ["Central"]


def test_stale_choice_token_cannot_resume_pending_request():
    orchestrator = _orchestrator()
    orchestrator._clarification_response(
        session_id="s1", intent=_intent(), kind="location_store",
        prompt="Which store?", options=[{"label": "Central", "values": ["Central"]}],
    )
    resumed, matched, error = orchestrator._resume_pending(
        "clar_stale", orchestrator.sessions.pending("s1")
    )
    assert resumed is None and matched
    assert "no longer valid" in error


def test_pending_state_isolated_between_sessions():
    memory = SessionMemory()
    memory.set_pending("a", {"id": "clar_a", "kind": "location", "options": []})
    assert memory.pending("a")["id"] == "clar_a"
    assert memory.pending("b") is None


def test_usage_aggregation_normalizes_none_without_masking_invalid_values():
    narration = Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5, calls=1)
    combined = _usage_or_zero(None) + _usage_or_zero(narration)
    assert combined.total_tokens == 5
    assert combined.calls == 1


def test_usage_aggregation_preserves_valid_llm_usage():
    llm = Usage(prompt_tokens=10, completion_tokens=4, total_tokens=14, calls=1)
    narration = Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3, calls=1)
    combined = _usage_or_zero(llm) + _usage_or_zero(narration)
    assert combined.prompt_tokens == 12
    assert combined.total_tokens == 17


def test_scenario_target_reply_preserves_original_scenario():
    from app.llm.schema import Scenario

    orchestrator = _orchestrator()
    scenario_intent = Intent(
        capability="what_if", metrics=[], filters=Filters(),
        time=TimeWindow(), scenario=Scenario(kind="demand_change_pct", value=20),
    )
    orchestrator._clarification_response(
        session_id="scenario", intent=scenario_intent, kind="scenario_target",
        prompt="Which product?", options=[], field="products",
    )
    resumed, matched, error = orchestrator._resume_pending(
        "Laptop A", orchestrator.sessions.pending("scenario")
    )
    assert matched and error is None
    assert resumed.scenario.kind == "demand_change_pct"
    assert resumed.scenario.value == 20
    assert resumed.filters.products == ["Laptop A"]


def test_natural_scenario_reply_merges_entities_without_replacing_capability_or_scenario():
    from app.llm.schema import Scenario

    base = Intent(
        capability="what_if", filters=Filters(), time=TimeWindow(),
        scenario=Scenario(kind="demand_change_pct", value=20),
    )
    parsed = Intent(
        capability="inventory_visibility",
        filters=Filters(products=["Catalog Laptop"], stores=["Central"]),
        time=TimeWindow(),
    )

    resumed = _merge_scenario_clarification_reply(base, parsed, "Show Catalog Laptop inventory at Central")
    assert resumed.capability == "what_if"
    assert resumed.scenario.kind == "demand_change_pct" and resumed.scenario.value == 20
    assert resumed.filters.products == ["Catalog Laptop"]
    assert resumed.filters.stores == ["Central"]


def test_short_product_reply_is_accepted_but_instruction_is_not_used_as_product_name():
    base = Intent(capability="what_if", filters=Filters(), time=TimeWindow())
    parsed = Intent(capability="inventory_visibility", filters=Filters(), time=TimeWindow())

    short = _merge_scenario_clarification_reply(base, parsed, "Catalog Laptop")
    assert short.filters.products == ["Catalog Laptop"]
    assert _merge_scenario_clarification_reply(base, parsed, "Show Catalog Laptop at Central") is None


def test_ambiguous_scenario_product_choices_are_catalog_backed_and_authorized():
    from app.context import UserContext
    from app.semantic.resolver import EntityResolver

    products = pd.DataFrame([
        {"product_id": 1, "sku": "SKU-1", "product_name": "Catalog Laptop A", "brand": "Dell", "category": "Laptops", "model": "A"},
        {"product_id": 2, "sku": "SKU-2", "product_name": "Catalog Laptop B", "brand": "HP", "category": "Laptops", "model": "B"},
    ])
    stores = pd.DataFrame([{"store_id": 10, "store_name": "Central", "city": "Delhi", "region": "North"}])
    orchestrator = Orchestrator(
        llm=object(), queries=object(), resolver=EntityResolver(products, stores),
        rules=Rules(), session_memory=SessionMemory(), dispatch_fn=lambda **kwargs: None,
    )
    choices = orchestrator._scenario_product_options(
        [1, 2], UserContext(persona="Leadership", authorized_product_ids=(2,)),
    )
    assert [choice["label"] for choice in choices] == ["Catalog Laptop B"]
    assert choices[0]["values"] == ["Catalog Laptop B"]


def test_period_clarification_parses_month_year_and_latest():
    assert parse_clarification_period("June 2026") == {"kind": "month", "month": 6, "year": 2026}
    assert parse_clarification_period("July") == {"kind": "month", "month": 7}
    assert parse_clarification_period("Latest inventory") == {"kind": "latest"}
    assert parse_clarification_period("This month") == {"kind": "this_month"}
    assert parse_clarification_period("Last month") == {"kind": "last_month"}
