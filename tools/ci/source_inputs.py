"""Refuse incomplete source imports instead of silently running fewer tests."""
from pathlib import Path


REQUIRED = (
    "docs/pricing/PRICING_REFERENCE_76_GAP_MATRIX_20261105.md",
    "docs/legal/PRIVACY_POLICY_APPENDIX_A.md",
    "apps/storefront_builder/qa/reference_capture.py",
    "apps/storefront_builder/qa/reference_packs.py",
    "apps/storefront_builder/management/commands/qa_storefront_builder.py",
    "apps/storefront_builder/management/commands/qa_storefront_builder_r4.py",
    "apps/stores/demo_assets/rasti_mode_demo/raw_user_catalog/QA_SOURCE_ASSETS.md",
    "apps/stores/demo_assets/rasti_mode_demo/scripts/build_inventory.py",
    "apps/stores/demo_assets/rasti_mode_demo/scripts/select_and_process_media.py",
)


def main():
    root = Path(__file__).resolve().parents[2]
    from qa_assets import restore
    print("QA_ASSETS_SHA256_VERIFIED files=%d" % restore(root))
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    for name in missing:
        print("MISSING CI SOURCE INPUT:", name)
    if missing:
        print("Restore these from the prepared source repository; do not remove tests or fabricate reference data.")
        return 1
    print("CI_SOURCE_INPUTS_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
