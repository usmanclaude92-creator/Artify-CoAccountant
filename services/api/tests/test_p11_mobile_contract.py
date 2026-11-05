"""Phase 11 backend extension: the upload-config distribution contract carries what a phone needs to render a form
(descriptions, default values, validation rules, reference targets). Additive: existing keys are unchanged."""

from __future__ import annotations

from conftest import make_company, new_owner
from test_p4_workspace import W, mk


def test_upload_config_carries_descriptions_defaults_validation_and_reference_targets(client, mailer) -> None:  # type: ignore[no-untyped-def]
    owner = new_owner(client, mailer, org_name="Mobile Contract Firm")
    a = make_company(owner, "MC")["id"]
    s = owner.post(W(a, "/nodes"), {"name": "Finance", "description": "Send monthly bank statements here."}).json()[
        "item"
    ]
    cat = owner.post(
        W(a, "/categories"),
        {
            "name": "Bank statement",
            "document_kind": "bank_statement",
            "node_id": s["id"],
            "description": "One PDF per month.",
        },
    ).json()["item"]
    mk_field = lambda body: owner.post(W(a, "/fields"), {"category_ids": [cat["id"]], **body})  # noqa: E731
    assert (
        mk_field(
            {"label": "Month", "data_type": "date", "required": True, "description": "Statement month end"}
        ).status_code
        == 201
    )
    assert (
        mk_field(
            {
                "label": "Amount limit",
                "data_type": "decimal",
                "default_value": "100",
                "validation": {"decimal_places": 2},
            }
        ).status_code
        == 201
    )
    assert (
        mk_field({"label": "Linked folder", "data_type": "reference", "reference_target": "workspace_node"}).status_code
        == 201
    )
    assert mk(owner, a, "Other")  # a second target
    body = owner.get(W(a, "/upload-config")).json()
    target = next(t for t in body["targets"] if t["label"] == "Finance")
    assert (
        target["description"] == "Send monthly bank statements here." and target["id"] == s["id"]
    )  # stable id, ordered by position
    assert body["categories"][0]["description"] == "One PDF per month."
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["month"]["description"] == "Statement month end" and fields["month"]["required"] is True
    assert fields["amount_limit"]["default_value"] == "100" and fields["amount_limit"]["validation"] == {
        "decimal_places": 2
    }
    assert fields["linked_folder"]["reference_target"] == "workspace_node"
    assert {"key", "label", "data_type", "required", "category_ids", "node_ids", "options"} <= set(
        fields["month"]
    )  # nothing removed
