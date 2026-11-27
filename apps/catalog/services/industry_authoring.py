"""Create validated versions without mutating installed template content."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.catalog.models import (
    IndustryTemplate, IndustryTemplateAttribute, IndustryTemplateAttributeValue,
    IndustryTemplateCategory, IndustryTemplateCategoryAttributeMapping,
    IndustryTemplateRecommendedOption,
)
from apps.catalog.services.template_validation_service import validate_and_persist


def _save(model, data, fields, **relations):
    if not isinstance(data, dict) or set(data) - set(fields.split()):
        raise ValidationError("ساختار تعریف معتبر نیست.")
    row = model(**data, **relations)
    row.full_clean()
    row.save()
    return row


@transaction.atomic
def create_version(data, *, source=None):
    if not isinstance(data, dict):
        raise ValidationError("ساختار قالب معتبر نیست.")
    header = data.copy()
    categories = header.pop("categories", [])
    attributes = header.pop("attributes", [])
    mappings = header.pop("mappings", [])
    recommendations = header.pop("recommendations", [])
    if not all(isinstance(items, list) and len(items) <= 500 for items in
               (categories, attributes, mappings, recommendations)):
        raise ValidationError("تعریف‌ها باید فهرست باشند.")
    slug = header.get("slug")
    if not isinstance(slug, str) or (source and slug != source.slug):
        raise ValidationError("شناسه صنف معتبر نیست و در نسخه جدید تغییر نمی‌کند.")
    family = IndustryTemplate.objects.filter(slug=slug)
    first = family.select_for_update().order_by("pk").first()
    if (source is None and first is not None) or (source is not None and first is None):
        raise ValidationError("صنف موجود است. از صفحه همان صنف نسخه جدید بسازید.")
    version = (family.aggregate(latest=Max("version"))["latest"] or 0) + 1
    template = _save(
        IndustryTemplate, header, "slug name description icon locale sector display_order default_section_keys",
        version=version, is_active=False,
    )
    by_code = {}
    parents = {}
    for item in categories:
        if not isinstance(item, dict):
            raise ValidationError("تعریف دسته معتبر نیست.")
        info = item.copy()
        parent = info.pop("parent_code", None)
        row = _save(IndustryTemplateCategory, info, "code name icon display_order", industry_template=template)
        by_code[row.code] = row
        parents[row.code] = parent
    for code, parent in parents.items():
        if parent is None:
            continue
        if not isinstance(parent, str) or parent not in by_code or parent == code:
            raise ValidationError("دسته والد معتبر نیست.")
        row = by_code[code]
        row.parent = by_code[parent]
        row.save(update_fields=["parent"])
    by_attribute = {}
    for item in attributes:
        if not isinstance(item, dict):
            raise ValidationError("تعریف ویژگی معتبر نیست.")
        info = item.copy()
        values = info.pop("values", [])
        if not isinstance(values, list) or len(values) > 500:
            raise ValidationError("مقادیر ویژگی باید فهرست باشند.")
        row = _save(IndustryTemplateAttribute, info, "code label data_type display_type unit is_variant_axis display_order",
                    industry_template=template)
        by_attribute[row.code] = row
        for value in values:
            _save(IndustryTemplateAttributeValue, value, "label value color_hex display_order", template_attribute=row)
    for model, records, fields in (
        (IndustryTemplateCategoryAttributeMapping, mappings,
         "group group_order display_order is_required is_filterable is_comparable is_searchable help_text placeholder"),
        (IndustryTemplateRecommendedOption, recommendations, "display_order"),
    ):
        for item in records:
            if not isinstance(item, dict):
                raise ValidationError("نگاشت معتبر نیست.")
            info = item.copy()
            category, attribute = info.pop("category_code", None), info.pop("attribute_code", None)
            if (not isinstance(category, str) or not isinstance(attribute, str)
                    or category not in by_code or attribute not in by_attribute):
                raise ValidationError("نگاشت به دسته یا ویژگی تعریف‌نشده اشاره می‌کند.")
            _save(model, info, fields, template_category=by_code[category],
                  template_attribute=by_attribute[attribute])
    result = validate_and_persist(template)
    if not result.is_valid:
        raise ValidationError([issue.message for issue in result.errors])
    return template
