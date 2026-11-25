"""RastiSi 3.3 product workbook and atomic, store-scoped import planning."""
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from openpyxl import load_workbook
from openpyxl.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from apps.catalog.models import Attribute, AttributeValue, Brand, Category, Product, ProductVariant
from apps.catalog.services.inventory_service import adjust_stock_manually
from apps.catalog.services.variant_engine_service import (
    VariantEngineError, add_manual_variant, add_option_value, add_product_option,
)
from apps.core.models import ImportJob, ImportRowResult
from apps.core.services import xlsx_utils
from apps.core.utils import normalization_key
from apps.dashboard.services.catalog_admin_service import default_vendor, generate_unique_slug
from apps.orders.models import TaxClass
from apps.stores.models import Store
from apps.subscriptions.services.enforcement import product_creation_budget

SHEET = "ورود محصولات"
LIST_SHEET = "فهرست‌ها"
LIST_ROW_OFFSET = 1_000_000
LIST_KEYS = ("category", "child", "brand", "axis1", "value1")
LIST_COLUMNS = {"category": 1, "child": 2, "brand": 4, "axis1": 5, "value1": 6}
LEGACY_LIST_COLUMNS = dict(zip(LIST_KEYS, range(3, 8), strict=True))
LIST_FIRST_ROW = 5
LIST_WIDTH = 9
KEYS = (
    "group", "name", "category", "child", "brand",
    "axis1", "value1", "axis2", "value2", "axis3", "value3",
    "price", "stock", "sku", "barcode", "weight", "status", "shipping",
    "tax", "specs", "seo_title", "seo_description", "product_id", "variant_id", "operation",
)
COMMON = ("name", "category", "child", "brand", "status", "shipping", "tax", "specs", "seo_title", "seo_description")
RS = ImportRowResult.RowStatus
STATUSES = {"فعال": Product.Status.ACTIVE, "غیرفعال": Product.Status.INACTIVE, "پیش‌نویس": Product.Status.DRAFT}
NO_TAX_CLASS = "— بدون دسته مالیاتی —"
OPERATIONS = {"": "auto", "خودکار": "auto", "ایجاد": "create", "به‌روزرسانی": "update"}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _template():
    return load_workbook(Path(__file__).with_name("templates") / "import-v3.3.xlsx", keep_links=False)


@lru_cache(maxsize=1)
def headers():
    template = _template()
    try:
        return tuple(cell.value for cell in template[SHEET][1])
    finally:
        template.close()


def template_columns():
    return [{"base_label": value.removesuffix(" *"), "required": "yes" if value.endswith(" *") else "no"}
            for value in headers()]


def _display(records, label):
    by_name = defaultdict(list)
    for obj in records:
        by_name[normalization_key(getattr(obj, label))].append(obj)
    return {obj.pk: getattr(obj, label) if len(group) == 1 else f"{getattr(obj, label)} [{obj.pk}]"
            for group in by_name.values() for obj in group}


class References:
    def __init__(self, store):
        self.categories = list(Category.objects.filter(store=store).order_by("name", "pk"))
        self.roots = [row for row in self.categories if row.parent_id is None]
        self.children = defaultdict(list)
        for row in self.categories:
            self.children[row.parent_id].append(row)
        self.brands = list(Brand.objects.filter(store=store).order_by("name", "pk"))
        self.axes = list(Attribute.objects.filter(store=store).prefetch_related("values").order_by("label", "pk"))
        self.taxes = list(TaxClass.objects.filter(store=store).order_by("name", "pk"))
        self.root_labels = _display(self.roots, "name")
        self.child_labels = {pk: name for children in self.children.values()
                             for pk, name in _display(children, "name").items()}
        self.brand_labels = _display(self.brands, "name")
        self.axis_labels = _display(self.axes, "label")
        self.tax_labels = _display(self.taxes, "name")


def _excel_literal(expression):
    # MATCH(..., 0) still interprets Excel wildcard characters.
    return f'SUBSTITUTE(SUBSTITUTE(SUBSTITUTE({expression},"~","~~"),"*","~*"),"?","~?")'


def _style_workbook(wb, list_end):
    title, guide, heading = "173B32", "DCEFE7", "27604F"
    stripe = ("FFFFFF", "F2F7F4")
    editable = ("FFF8E5", "F7ECCA")
    ink = "20362E"
    for sheet in wb:
        sheet.sheet_view.rightToLeft = True
        sheet.sheet_view.showGridLines = False
        sheet.sheet_view.topLeftCell = "A1"
        for selection in sheet.sheet_view.selection:
            if selection.pane in (None, "bottomRight", "bottomLeft"):
                selection.activeCell = sheet.freeze_panes or "A1"
                selection.sqref = selection.activeCell

        grid = sheet.title in (SHEET, "نمونه تکمیل‌شده")
        lists = sheet.title == LIST_SHEET
        last_col = 25 if grid else LIST_WIDTH if lists else 6
        merges = {area.start_cell.coordinate: area for area in sheet.merged_cells.ranges}
        for row in sheet.iter_rows(max_col=last_col):
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                r, c = cell.row, cell.column
                band = merges.get(cell.coordinate)
                if lists and c == 3 and not band:
                    fill, foreground, bold = "FFFFFF", ink, False
                    cell.border = Border()
                elif r == 1 and (band or grid):
                    fill, foreground, bold = title, "FFFFFF", True
                elif band and band.min_col == 1 and r == 2:
                    fill, foreground, bold = guide, ink, True
                elif (lists and r == 4) or (band and band.min_col == 1) or (sheet.title == "قرارداد فنی" and r == 5):
                    fill, foreground, bold = heading, "FFFFFF", True
                elif (sheet.title == SHEET and r > 1 and c <= 22) or (lists and LIST_FIRST_ROW <= r <= list_end and c <= 6):
                    fill, foreground, bold = editable[r % 2], ink, False
                else:
                    fill, foreground, bold = stripe[r % 2], ink, False
                fill_style = PatternFill("solid", fgColor=fill)
                cell.fill = fill_style
                cell.font = Font(name="Tahoma", size=15 if r == 1 and band else 10, color=foreground, bold=bold)
                ltr = (grid and r > 1 and c in (1, 12, 13, 14, 15, 16, 23, 24)) or (
                    sheet.title == "قرارداد فنی" and 6 <= r <= 30 and c in (1, 3)
                )
                cell.alignment = Alignment(
                    horizontal="center" if ltr else "right", vertical="center", readingOrder=1 if ltr else 2,
                    wrap_text=not ltr,
                )
                if band:
                    for band_row in sheet.iter_rows(min_row=band.min_row, max_row=band.max_row,
                                                   min_col=band.min_col, max_col=band.max_col):
                        for band_cell in band_row:
                            band_cell.fill = fill_style
        if grid:
            sheet.row_dimensions[1].height = 35
    wb["راهنما"].row_dimensions[2].height = 30
    wb["قرارداد فنی"].row_dimensions[2].height = 30
    wb[LIST_SHEET].row_dimensions[1].height = 32
    wb[LIST_SHEET].row_dimensions[2].height = 30
    wb[LIST_SHEET].row_dimensions[4].height = 28


def workbook(store):
    wb = _template()
    ref = References(store)
    lists = wb["فهرست‌ها"]
    contract = wb["قرارداد فنی"]
    product = wb[SHEET]
    guide = wb["راهنما"]
    guide["A2"] = "تعریف‌های جدید را در «فهرست‌ها» وارد کنید. سپس در «ورود اطلاعات» همین فروشگاه فایل را پیش‌نمایش و ثبت نهایی کنید."
    guide["C37"] = "در «فهرست‌ها» نام دسته اصلی را یک‌بار در ستون A بنویسید."
    guide["D37"] = "زیرگروه‌ها را در ستون B از همان ردیف، زیر هم وارد کنید."
    guide["D38"] = "دسته بعدی را پس از آخرین زیرگروه در ستون A آغاز کنید."
    guide["C40"] = "در «فهرست‌ها» ویژگی را در E و مقادیرش را در F بنویسید."
    contract["A2"] = "قالب ورود محصولات ۳.۳. تعریف‌های تازه فقط پس از تأیید نهایی ذخیره می‌شوند."
    old_headings = [cell.value for cell in lists[4][:8]]
    for merged in ("A1:H1", "A2:H2"):
        lists.unmerge_cells(merged)
        lists.merge_cells(merged.replace("H", "I"))
    for col, title in enumerate((*old_headings[2:4], None, *old_headings[4:], *old_headings[:2]), 1):
        lists.cell(4, col).value = title
    for col, width in zip("ABCDEFGHI", (25, 28, 5, 24, 21, 23, 24, 17, 14), strict=True):
        lists.column_dimensions[col].width = width
        lists.column_dimensions[col].hidden = False
    notes = []
    for merged in list(lists.merged_cells.ranges):
        if merged.min_row >= 5:
            notes.append((merged.min_row, lists.cell(merged.min_row, merged.min_col).value))
            lists.unmerge_cells(str(merged))
    for row in lists.iter_rows(min_row=5, max_col=13):
        for cell in row:
            if cell.value is not None:
                cell.value = None
    for row in wb["نمونه تکمیل‌شده"].iter_rows(min_row=2, max_col=25):
        for cell in row:
            if cell.value is not None:
                cell.value = None
    # Independent of any tenant data: a vendor can copy the structure of this demo row.
    for col, value in {1: "DEMO-001", 2: "کالای نمونه", 3: "دسته نمونه", 12: 250000, 13: 10}.items():
        wb["نمونه تکمیل‌شده"].cell(2, col, value)

    def write(col, values):
        for i, value in enumerate(values, 5):
            if value is not None:
                xlsx_utils.set_value(lists.cell(i, col), value)
        return i if values else 4

    def grouped(parents, label, children):
        for parent in parents:
            values = list(children(parent))
            yield label[parent.pk], values[0] if values else None
            yield from ((None, value) for value in values[1:])

    roots = [obj for obj in ref.roots if obj.is_active]
    active_ids = {c.pk for c in ref.categories if c.is_active}
    axes = [obj for obj in ref.axes if obj.is_active and obj.is_variant_axis
            and (obj.category_id is None or obj.category_id in active_ids)]
    category_rows = list(grouped(roots, ref.root_labels,
                                 lambda root: (ref.child_labels[x.pk] for x in ref.children[root.pk] if x.is_active)))
    axis_rows = list(grouped(axes, ref.axis_labels,
                             lambda axis: (value.label for value in axis.values.all() if value.is_active)))
    last = max(write(8, list(STATUSES)), write(9, ["بله", "خیر"]),
               write(1, [row[0] for row in category_rows]), write(2, [row[1] for row in category_rows]),
               write(4, [ref.brand_labels[b.pk] for b in ref.brands if b.is_active]),
               write(5, [row[0] for row in axis_rows]), write(6, [row[1] for row in axis_rows]),
               write(7, [ref.tax_labels[t.pk] for t in ref.taxes if t.is_active] + [NO_TAX_CLASS]))
    limit = max(120, last + 40)
    # Notes belong in the guide: column A of Lists now carries actual categories.
    for index, (_, note) in enumerate(sorted(notes), guide.max_row + 2):
        if note:
            xlsx_utils.set_value(guide.cell(index, 1), note)
            guide.merge_cells(start_row=index, start_column=1, end_row=index, end_column=6)
            guide.row_dimensions[index].height = 32
    helper_end = limit
    for source, detail, label, start, count, packed, packed_start in (
        ("A", "B", "H", "I", "J", "T", "V"),
        ("E", "F", "L", "M", "N", "U", "W"),
    ):
        children = f"'فهرست‌ها'!${detail}$5:${detail}${limit}"
        for row in range(5, limit + 1):
            contract[f"{packed}{row}"] = (
                f'=IFERROR(INDEX({children},_xlfn.AGGREGATE(15,6,(ROW({children})-4)/'
                f'({children}<>""),ROWS(${packed}$5:{packed}{row}))),"")'
            )
        for row in range(5, helper_end + 2):
            span = f"'فهرست‌ها'!${source}$5:${source}${limit}"
            contract[f"{label}{row}"] = (
                f'=IFERROR(INDEX({span},_xlfn.AGGREGATE(15,6,(ROW({span})-4)/({span}<>""),'
                f'ROWS(${label}$5:{label}{row}))),"")'
            )
            contract[f"{start}{row}"] = f'=IF({label}{row}="","",MATCH({_excel_literal(f"{label}{row}")},{span},0))'
            contract[f"{packed_start}{row}"] = (
                f'=IF({label}{row}="","",IF({start}{row}=1,1,1+'
                f'COUNTIF(INDEX({children},1):INDEX({children},{start}{row}-1),"?*")))'
            )
            contract[f"{count}{row}"] = (
                f'=IF({label}{row}="","",COUNTIF(INDEX({children},{start}{row}):'
                f'INDEX({children},IF({label}{row+1}="",ROWS({children}),{start}{row+1}-1)),"?*"))'
            )
    blank_cell = "'قرارداد فنی'!$O$5"

    def dependent(source, match, indices, counts):
        offset = f"IFERROR(INDEX({indices},{match})-1,0)"
        size = f"IFERROR(INDEX({counts},{match}),0)"
        return (
            f'OFFSET(INDIRECT(IF({size}>0,"{source}","{blank_cell}")),'
            f'IF({size}>0,{offset},0),0,MAX(1,{size}),1)'
        )

    # Existing category restrictions are also checked server-side.
    next_value = 5
    contexts = [("", "", set())]
    for root in roots:
        root_label = ref.root_labels[root.pk]
        contexts.append((root_label, "", {root.pk}))
        contexts.extend((root_label, ref.child_labels[child.pk], {root.pk, child.pk})
                        for child in ref.children[root.pk] if child.is_active)
    # Detect axis names first entered in this workbook. An existing axis
    # retains its store category restriction even if its row is moved.
    for row, axis in enumerate(axes, 5):
        xlsx_utils.set_text(contract[f"X{row}"], ref.axis_labels[axis.pk])
    known_axes = f"$X$5:$X${max(5, len(axes) + 4)}"
    candidates = f"$Y$5:$Y${limit}"
    for row in range(5, limit + 1):
        source = f"'فهرست‌ها'!E{row}"
        contract[f"Y{row}"] = f'=IF({source}="","",IF(ISNUMBER(MATCH({_excel_literal(source)},{known_axes},0)),"",{source}))'
        contract[f"Z{row}"] = (
            f'=IFERROR(INDEX({candidates},_xlfn.AGGREGATE(15,6,(ROW({candidates})-4)/'
            f'({candidates}<>""),ROWS($Z$5:Z{row}))),"")'
        )
    contract["AA5"] = f'=COUNTIF({candidates},"?*")'

    # Share a contiguous range for contexts with identical allowed axes.
    # Room for new axes covers the editable list range in this workbook.
    slots = limit - 4
    profiles = {}
    for row, (root, child, allowed) in enumerate(contexts, 5):
        labels = tuple(ref.axis_labels[a.pk] for a in axes if not a.category_id or a.category_id in allowed)
        if labels not in profiles:
            profiles[labels] = next_value - 4
            for label in labels:
                xlsx_utils.set_text(contract[f"S{next_value}"], label)
                next_value += 1
            start = next_value
            for index in range(slots):
                contract[f"S{start + index}"] = f'=IF(Z{5 + index}="","",Z{5 + index})'
            next_value += slots
        xlsx_utils.set_text(contract[f"P{row}"], f"{len(root)}:{root}{child}")
        contract[f"Q{row}"] = profiles[labels]
        contract[f"R{row}"] = f'={len(labels)}+$AA$5'
    end = len(contexts) + 4
    for col in "HIJKLMNOPQRSTUVWXYZ":
        contract.column_dimensions[col].hidden = True
    contract.column_dimensions["AA"].hidden = True

    def input_column(column, first=1):
        return f"'{SHEET}'!${column}${first}:${column}$400"

    def current(column):
        return f"INDEX({input_column(column)},ROW())"

    def picked(column):
        position = f"MATCH({_excel_literal(current("A"))},{input_column('A', 2)},0)"
        return f'IF({current(column)}<>"",{current(column)},IFERROR(INDEX({input_column(column, 2)},{position}),""))'

    def technical(column, last):
        address = f"'قرارداد فنی'!${column}$5:${column}${last}"
        return 'INDIRECT("' + address + '")'

    match_category = f'MATCH({_excel_literal(picked("C"))},{technical("H", helper_end)},0)'
    root_key = f'LEN({picked("C")})&":"&{picked("C")}'
    context_keys = technical("P", end)
    root_match = f'MATCH({_excel_literal(root_key)},{context_keys},0)'
    full_match = f'MATCH({_excel_literal(root_key + "&" + picked("D"))},{context_keys},0)'
    match_axis = f'IFERROR({full_match},IFERROR({root_match},1))'
    category_rule = dependent("'قرارداد فنی'!$T$5", match_category, technical("V", helper_end), technical("J", helper_end))
    axis_rule = dependent("'قرارداد فنی'!$S$5", match_axis, technical("Q", end), technical("R", end))
    for dv in product.data_validations.dataValidation:
        if dv.type != "list":
            continue
        column = str(dv.sqref).split("2", 1)[0]
        formula = dv.formula1.replace("$120", f"${limit}").replace("$64", f"${helper_end}")
        named = column in {"D", "F", "G", "H", "I", "J", "K"}
        list_column = {"E": "D", "Q": "H", "R": "I", "S": "G"}.get(column)
        if list_column:
            address = f"'فهرست‌ها'!${list_column}$5"
            formula = f'OFFSET(INDIRECT("{address}"),0,0,MAX(1,COUNTA(INDIRECT("{address}:${list_column}${limit}"))),1)'
        elif column == "D":
            formula = category_rule
        elif column in {"F", "H", "J"}:
            formula = axis_rule
        elif column in {"G", "I", "K"}:
            axis_column = {"G": "F", "I": "H", "K": "J"}[column]
            match = f'MATCH({_excel_literal(current(axis_column))},{technical("L", helper_end)},0)'
            formula = dependent("'قرارداد فنی'!$U$5", match, technical("W", helper_end), technical("N", helper_end))

        if named:
            # Excel's validation source field stays short. ROW() makes the
            # named range resolve against whichever row is being edited.
            name = f"V33_Choices_{column}"
            wb.defined_names.add(DefinedName(name, attr_text=formula))
            formula = name
        dv.formula1 = formula
        # Typing a proposed reference is allowed; validation is authoritative on upload.
        dv.showErrorMessage = column in {"Q", "R", "S", "Y"}
    _style_workbook(wb, limit)
    wb.calculation.fullCalcOnLoad = True
    return xlsx_utils.workbook_to_bytes(wb)


def parse(actual_headers, sheet_rows):
    if tuple(actual_headers) != headers():
        raise xlsx_utils.XlsxReadError("سرستون‌های شیت «ورود محصولات» باید مطابق قالب ۳.۳ باقی بمانند.")
    for row in sheet_rows:
        cells = dict(zip(KEYS, row.cells))
        cells["__row_number__"] = row.number
        cells["__v33__"] = True
        cells["__raw__"] = list(zip(actual_headers, row.cells))
        cells["__errors__"] = [error(KEYS[i], "سلول فرمولی یا دارای خطا مجاز نیست.")
                               for i in row.formula_columns if i < len(KEYS)]
        yield cells


@lru_cache(maxsize=1)
def list_headers():
    wb = _template()
    try:
        return tuple(cell.value for cell in wb[LIST_SHEET][4][2:7])
    finally:
        wb.close()


def parse_lists(fileobj):
    headers, rows = xlsx_utils.read_xlsx_table(
        fileobj, sheet_names=(LIST_SHEET,), require_sheet=True, first_header_row=4,
    )
    columns = next((mapping for mapping in (LIST_COLUMNS, LEGACY_LIST_COLUMNS)
                    if tuple(headers[i - 1] for i in mapping.values() if i <= len(headers)) == list_headers()), None)
    if columns is None:
        raise xlsx_utils.XlsxReadError("سرستون‌های شیت «فهرست‌ها» باید مطابق قالب ۳.۳ باقی بمانند.")
    for row in rows:
        cells = {key: row.cells[index - 1] for key, index in columns.items()}
        errors = [error(key, "سلول فرمولی یا دارای خطا مجاز نیست.")
                  for key, index in columns.items() if index - 1 in row.formula_columns]
        if errors or any(value.strip() for value in cells.values()):
            yield {
                **cells, "__v33__": True, "__v33_list__": True,
                "__row_number__": LIST_ROW_OFFSET + row.number,
                "__list_columns__": columns,
                "__errors__": errors, "__raw__": list(zip(list_headers(), cells.values(), strict=True)),
            }


def list_error_at(message, columns):
    key = KEYS[ord(message[0]) - ord("A")]
    return f"{get_column_letter(columns[key])}{message[1:]}"


def identity(record, label):
    return record.pk if record is not None else normalization_key(label)


def definition_key(kind, name, parent=None):
    return kind, parent, normalization_key(name)


def require_definition(declared, column, name, parent=None):
    kind = {"axis2": "axis1", "axis3": "axis1", "value2": "value1", "value3": "value1"}.get(column, column)
    if definition_key(kind, name, parent) not in declared:
        raise InvalidRow(column, "گزینه یا وابستگی آن در شیت «فهرست‌ها» تعریف نشده است.")


def plan_lists(store, rows):
    from apps.dashboard.services.import_service import RowOutcome

    ref = References(store)
    roots = {
        "category": (ref.roots, ref.root_labels),
        "axis1": (ref.axes, ref.axis_labels),
        "brand": (ref.brands, ref.brand_labels),
    }
    parent_columns = {"child": "category", "value1": "axis1"}
    contexts, seen, declared, outcomes, products = {}, set(), set(), [], []
    for row in rows:
        if not row.get("__v33_list__"):
            products.append(row)
            continue
        additions, labels, errors = [], [], list(row["__errors__"])
        for kind in LIST_KEYS:
            parent = None
            parent_record = None
            if any(message.startswith(f"{error(kind, '')[0]}:") for message in row["__errors__"]):
                contexts.pop(kind, None)
                continue
            try:
                if kind in roots and row.get(kind, "").strip():
                    contexts.pop(kind, None)
                name = text_value(row, kind, 160)
                if not name:
                    continue
                if kind in roots:
                    records, display = roots[kind]
                else:
                    parent_kind = parent_columns[kind]
                    if parent_kind not in contexts:
                        raise InvalidRow(kind, "ابتدا والد این گزینه را در ستون مجاور تعریف کنید.")
                    parent, parent_record = contexts[parent_kind]
                    if kind == "child":
                        records = ref.children[parent_record.pk] if parent_record else []
                        display = ref.child_labels
                    else:
                        records = list(parent_record.values.all()) if parent_record else []
                        display = _display(records, "label")
                record = lookup(name, records, display, kind, create=True)
                if kind == "axis1" and record and not record.is_variant_axis:
                    raise InvalidRow(kind, "این ویژگی محور تنوع نیست.")
                if kind == "value1" and record is None and parent_record and parent_record.data_type not in Attribute.CHOICE_DATA_TYPES:
                    raise InvalidRow(kind, "افزودن مقدار به این نوع ویژگی مجاز نیست.")
                if kind == "child" and record is None and any(
                    c.parent_id and normalization_key(c.name) == normalization_key(name) for c in ref.categories
                ):
                    raise InvalidRow(kind, "این زیرگروه به دسته اصلی دیگری تعلق دارد.")
                parent_identity = parent_record.pk if parent_record else (parent[2] if parent else None)
                key = (kind, parent_identity, identity(record, name))
                if key in seen:
                    raise InvalidRow(kind, "تعریف تکراری در شیت «فهرست‌ها».")
                if record is None:
                    text_value(row, kind, 120)
                seen.add(key)
                if kind in ("category", "axis1"):
                    contexts[kind] = (definition_key(kind, name), record)
                if record is None:
                    declared.add(definition_key(kind, name, parent_identity))
                    additions.append((kind, name, parent, getattr(parent_record, "pk", None)))
                    labels.append(f"{list_headers()[LIST_KEYS.index(kind)]}: {name}")
            except InvalidRow as exc:
                errors.append(str(exc))
        columns = row.get("__list_columns__", LEGACY_LIST_COLUMNS)
        outcomes.append((RowOutcome(
            row_number=row["__row_number__"], source_identifier="", status=RS.INVALID if errors else RS.VALID if additions else RS.SKIPPED,
            errors=[list_error_at(message, columns) for message in errors], warnings=[f"تعریف جدید پس از تأیید: {label}" for label in labels],
            normalized_data_summary={
                "action": "define", "product_name": LIST_SHEET, "v33": True,
                "sheet_row": row["__row_number__"] - LIST_ROW_OFFSET, "list_columns": list(columns.values()),
            },
        ), additions))
    return products, outcomes, declared


def save_definition(store, kind, name, parent_id):
    if kind == "value1":
        value = AttributeValue(attribute_id=parent_id, label=name, value=name)
        value.full_clean()
        value.save()
        return value.pk
    if kind == "axis1":
        return _new_named(
            Attribute, store=store, label="label", name=name,
            defaults={"is_variant_axis": True, "data_type": Attribute.DataType.SELECT},
        ).pk
    if kind == "brand":
        return _new_named(Brand, store=store, label="name", name=name).pk
    return _new_named(Category, store=store, label="name", name=name, filters={"parent_id": parent_id}).pk


def error(key, message):
    return f"{get_column_letter(KEYS.index(key) + 1)}: {message}"


class InvalidRow(ValueError):
    def __init__(self, key, message):
        super().__init__(error(key, message))


def text_value(row, key, size, required=False):
    value = row.get(key, "").strip()
    if (required and not value) or len(value) > size or any(ord(c) < 32 for c in value):
        raise InvalidRow(key, f"مقدار باید معتبر و حداکثر {size} نویسه باشد.")
    return value


def integer(row, key, maximum, required=False):
    raw = row.get(key, "").strip().translate(DIGITS)
    if not raw and not required:
        return None
    if not re.fullmatch(r"[0-9]{1,19}", raw) or int(raw) > maximum:
        raise InvalidRow(key, f"عدد صحیح بین صفر و {maximum} لازم است.")
    return int(raw)


def lookup(name, records, labels, column, *, create=False):
    key = normalization_key(name)
    matching = [obj for obj in records if normalization_key(labels[obj.pk]) == key]
    if not matching:
        matching = [obj for obj in records if normalization_key(getattr(obj, "label", None) or obj.name) == key]
    if not matching:
        matching = [obj for obj in records if key in {
            normalization_key(getattr(obj, "slug", "")), normalization_key(getattr(obj, "code", "")),
        } or name == str(obj.pk)]
    if len(matching) == 1 and matching[0].is_active:
        return matching[0]
    if matching or not create:
        raise InvalidRow(column, "گزینه یکتا، فعال و متعلق به همین فروشگاه نیست.")
    if name.isdecimal() or re.search(r"\s\[[0-9]+\]$", name):
        raise InvalidRow(column, "شناسه این گزینه برای این فروشگاه یا وابستگی معتبر نیست.")
    return None


def stable_variant_sku(group, pairs):
    packed = "\x1f".join([group, *(f"{attribute}\x1e{value}" for attribute, value in pairs)])
    return "V33-" + sha256(packed.encode()).hexdigest()[:32]


@dataclass
class Line:
    number: int
    group: str
    name: str = ""
    action: str = ""
    errors: list = field(default_factory=list)
    target: object = None
    price: int | None = None
    stock: int | None = None
    weight: int | None = None
    barcode: str = ""
    sku: str = ""
    pairs: list = field(default_factory=list)


@dataclass
class Group:
    code: str
    product: Product | None
    common: dict
    lines: list = field(default_factory=list)
    root: Category | None = None
    child: Category | None = None
    brand: Brand | None = None
    tax: TaxClass | None = None
    specs: list = field(default_factory=list)


def _specs(text):
    result = {}
    for item in re.split("[؛;]", text):
        if not item.strip():
            continue
        label, sep, value = item.partition(":")
        label, value = label.strip(), value.strip()
        key = normalization_key(label)
        if not sep or not label or not value or len(label) > 100 or len(value) > 300 or key in result:
            raise InvalidRow("specs", "هر مشخصه باید نام یکتا و مقدار، به شکل «نام: مقدار»، داشته باشد.")
        result[key] = (label, value)
    return list(result.values())


def _common(group, ref, declared):
    data, product = group.common, group.product
    if not product and not data.get("name"):
        raise InvalidRow("name", "نام کالای جدید الزامی است.")
    category_name, child_name, brand_name = (data.get(k, "") for k in ("category", "child", "brand"))
    if category_name:
        group.root = lookup(category_name, ref.roots, ref.root_labels, "category", create=True)
        if not group.root:
            require_definition(declared, "category", category_name)
        if child_name:
            siblings = ref.children[group.root.pk] if group.root else []
            group.child = lookup(child_name, siblings, ref.child_labels, "child", create=True)
            if not group.child:
                if any(c.parent_id and normalization_key(c.name) == normalization_key(child_name) for c in ref.categories):
                    raise InvalidRow("child", "این زیرگروه به دسته انتخاب‌شده تعلق ندارد.")
                require_definition(declared, "child", child_name, identity(group.root, category_name))
    elif not product or child_name:
        raise InvalidRow("category", "دسته اصلی الزامی است.")
    if brand_name:
        group.brand = lookup(brand_name, ref.brands, ref.brand_labels, "brand", create=True)
        if not group.brand:
            require_definition(declared, "brand", brand_name)
    if data.get("status") and data["status"] not in STATUSES:
        raise InvalidRow("status", "وضعیت معتبر نیست.")
    if data.get("shipping") and data["shipping"] not in ("بله", "خیر"):
        raise InvalidRow("shipping", "بله یا خیر لازم است.")
    if data.get("tax") and data["tax"] != NO_TAX_CLASS:
        group.tax = lookup(data["tax"], ref.taxes, ref.tax_labels, "tax")
    group.specs = _specs(data.get("specs", ""))


def _axis_pairs(row, group, ref, declared):
    pairs = []
    category = group.child or group.root or (group.product.category if group.product and not group.common.get("category") else None)
    allowed_category_ids = set()
    while category and category.pk not in allowed_category_ids:
        allowed_category_ids.add(category.pk)
        category = category.parent
    for index in range(1, 4):
        axis_key, value_key = f"axis{index}", f"value{index}"
        axis_name, value_name = (text_value(row, key, 150) for key in (axis_key, value_key))
        if not axis_name and not value_name:
            continue
        if not axis_name or not value_name:
            raise InvalidRow(axis_key if not axis_name else value_key, "ویژگی و مقدار باید جفت وارد شوند.")
        axis = lookup(axis_name, ref.axes, ref.axis_labels, axis_key, create=True)
        if axis and (not axis.is_variant_axis or (axis.category_id and axis.category_id not in allowed_category_ids)):
            raise InvalidRow(axis_key, "این ویژگی محور مجاز دسته انتخاب‌شده نیست.")
        axis_identity = str(axis.pk) if axis else normalization_key(axis_name)
        if any(p[0] == axis_identity for p in pairs):
            raise InvalidRow(axis_key, "محور تکراری است.")
        value = None
        if axis:
            options = list(axis.values.all())
            value = lookup(value_name, options, _display(options, "label"), value_key, create=True)
            if not value and axis.data_type not in Attribute.CHOICE_DATA_TYPES:
                raise InvalidRow(value_key, "افزودن مقدار به این نوع ویژگی مجاز نیست.")
        else:
            require_definition(declared, axis_key, axis_name)
        if not value:
            require_definition(declared, value_key, value_name, identity(axis, axis_name))
        pairs.append((axis_identity, axis, axis_name, value, value_name))
    return pairs


def _combination(pairs):
    return tuple(sorted((str(axis.pk) if axis else normalization_key(name),
                         normalization_key(value.label if value else label))
                        for _, axis, name, value, label in pairs))


def _variant_matches(product):
    matches = {}
    for variant in product.variants.prefetch_related("option_values__option", "option_values__option_value"):
        key = tuple(sorted((str(link.option.attribute_id) if link.option.attribute_id else normalization_key(link.option.label),
                            normalization_key(link.option_value.label)) for link in variant.option_values.all()))
        if key:
            matches[key] = variant
    return matches


def plan(store, rows, mode, declared):
    ref = References(store)
    groups, lines, signatures, explicit_skus = {}, [], defaultdict(set), set()
    product_cache, variant_cache = {}, {}
    for row in rows:
        number = row["__row_number__"]
        line = Line(number, row.get("group", ""), row.get("name", ""), errors=list(row["__errors__"]))
        lines.append(line)
        if line.errors:
            continue
        try:
            code = text_value(row, "group", 40, required=True)
            line.group = code
            operation = OPERATIONS.get(row.get("operation", ""))
            if operation is None:
                raise InvalidRow("operation", "عملیات معتبر نیست.")
            product_id = integer(row, "product_id", 2**63 - 1)
            variant_id = integer(row, "variant_id", 2**63 - 1)
            if (product_id is not None or variant_id is not None) and operation != "update":
                raise InvalidRow("operation", "شناسه داخلی فقط با عملیات به‌روزرسانی مجاز است.")
            if code not in product_cache:
                product_cache[code] = Product.objects.filter(store=store, sku=code).first()
            product = product_cache[code]
            if product_id is not None and (not product or product.pk != product_id):
                raise InvalidRow("product_id", "شناسه با کد گروه محصول این فروشگاه مطابق نیست.")
            common = {key: text_value(row, key, size) for key, size in zip(
                COMMON, (220, 160, 160, 160, 20, 10, 160, 5000, 70, 160), strict=True,
            )}
            if code not in groups:
                group = Group(code, product, common)
                _common(group, ref, declared)
                groups[code] = group
            else:
                group = groups[code]
                for key, value in common.items():
                    if value and value != group.common[key]:
                        raise InvalidRow(key, "اطلاعات مشترک کالا باید در ردیف اول تعیین شود و در ردیف‌های تنوع متناقض نباشد.")
            line.name = group.common["name"] or (product.name if product else "")
            line.sku = text_value(row, "sku", 64)
            axis_row = row
            if product and operation == "update" and (variant_id is not None or line.sku) and not any(
                row.get(f"axis{i}") or row.get(f"value{i}") for i in range(1, 4)
            ) and product.product_type == Product.ProductType.VARIABLE:
                variants = ProductVariant.objects.filter(store=store, product=product)
                identified = variants.filter(pk=variant_id).first() if variant_id is not None else variants.filter(sku=line.sku).first()
                if not identified:
                    raise InvalidRow("variant_id" if variant_id is not None else "sku", "تنوع برای این کالا پیدا نشد.")
                links = list(identified.option_values.select_related("option", "option_value").order_by("option__position"))
                if not 1 <= len(links) <= 3:
                    raise InvalidRow("variant_id", "تنوع موجود با محورهای قالب ۳.۳ سازگار نیست.")
                axis_row = dict(row)
                for i, link in enumerate(links, 1):
                    axis_row[f"axis{i}"] = ref.axis_labels.get(link.option.attribute_id, link.option.label)
                    axis_row[f"value{i}"] = link.option_value.label
            line.pairs = _axis_pairs(axis_row, group, ref, declared)
            combo = _combination(line.pairs)
            for key, labels in (
                ("axis1", [axis.label if axis else name for _, axis, name, _, _ in line.pairs]),
                ("value1", [value.label if value else name for _, _, _, value, name in line.pairs]),
            ):
                if len(" / ".join(labels)) > 60:
                    raise InvalidRow(key, "مجموع طول عنوان‌ها برای مدل تنوع بیش از ۶۰ نویسه است.")
            if combo in signatures[code]:
                raise InvalidRow("group", "این کالا یا ترکیب تنوع در فایل تکرار شده است.")
            if group.lines and (not combo or {pair[0] for pair in combo} != {p[0] for p in _combination(group.lines[0].pairs)}):
                raise InvalidRow("axis1", "تمام تنوع‌های یک کالا باید مجموعه محورهای یکسان داشته باشند.")
            if product and bool(combo) != (product.product_type == Product.ProductType.VARIABLE):
                raise InvalidRow("axis1", "تبدیل نوع کالای موجود در این ورود مجاز نیست.")
            if product and combo:
                axes = {str(o.attribute_id) if o.attribute_id else normalization_key(o.label)
                        for o in product.options.filter(is_active=True)}
                if axes != {pair[0] for pair in combo}:
                    raise InvalidRow("axis1", "محورهای فایل با محورهای فعال محصول موجود مطابق نیستند.")
                if product.pk not in variant_cache:
                    variant_cache[product.pk] = _variant_matches(product)
            target = variant_cache.get(product.pk, {}).get(combo) if product and combo else product
            if not combo and line.sku and line.sku != code:
                raise InvalidRow("sku", "SKU کالای ساده باید برابر کد گروه باشد.")
            if combo and not line.sku and not target:
                line.sku = stable_variant_sku(code, sorted(
                    (normalization_key(axis.label if axis else name),
                     normalization_key(value.label if value else label))
                    for _, axis, name, value, label in line.pairs
                ))
            if combo and line.sku:
                sku_target = ProductVariant.objects.filter(store=store, sku=line.sku).first()
                if sku_target and (not target or sku_target.pk != target.pk):
                    raise InvalidRow("sku", "SKU به ترکیب دیگری در فروشگاه اختصاص دارد.")
                if line.sku in explicit_skus:
                    raise InvalidRow("sku", "SKU تنوع در فایل تکرار شده است.")
            if variant_id is not None and (not combo or not target or target.pk != variant_id):
                raise InvalidRow("variant_id", "شناسه تنوع با ترکیب این کالا در فروشگاه مطابق نیست.")
            if (operation == "update" or mode == ImportJob.Mode.UPDATE_ONLY) and not target:
                raise InvalidRow("operation", "کالا یا تنوع موجود برای به‌روزرسانی پیدا نشد.")
            if operation == "create" and target or operation == "update" and mode == ImportJob.Mode.CREATE_ONLY:
                raise InvalidRow("operation", "عملیات با رکورد موجود یا حالت ورود سازگار نیست.")
            line.action = "update" if target and operation == "update" else ("skip" if target else "create")
            line.target = target
            line.price = integer(row, "price", 999_999_999_999, required=line.action == "create")
            if line.action == "update" and not combo and line.price is not None and product.active_discount_percent():
                raise InvalidRow("price", "ابتدا تخفیف فعلی کالای ساده را در پنل تعیین تکلیف کنید.")
            line.stock = integer(row, "stock", 2**31 - 1, required=line.action == "create")
            line.weight = integer(row, "weight", 2**31 - 1)
            line.barcode = text_value(row, "barcode", 64)
            signatures[code].add(combo)
            if combo and line.sku:
                explicit_skus.add(line.sku)
            group.lines.append(line)
        except InvalidRow as exc:
            line.errors.append(str(exc))
    creates = sum(bool(not group.product and group.lines) for group in groups.values())
    budget = product_creation_budget(store)
    limit = "تعداد کالاهای جدید از سقف پلن فروشگاه بیشتر است." if budget is not None and creates > budget else ""
    return groups, lines, limit


def _new_named(model, *, store, label, name, filters=None, defaults=None):
    filters = filters or {}
    matching = [obj for obj in model.objects.filter(store=store, **filters)
                if normalization_key(getattr(obj, label)) == normalization_key(name)]
    if len(matching) == 1 and matching[0].is_active:
        return matching[0]
    if matching:
        raise ValueError("گزینه هم‌نام یا غیرفعال هم‌زمان ثبت شد.")
    code_key = "code" if model is Attribute else "slug"
    code = generate_unique_slug(model, name, store=store) if code_key == "slug" else (
        "v33-" + sha256(normalization_key(name).encode()).hexdigest()[:28]
    )
    record = model(store=store, **{label: name, code_key: code}, **filters, **(defaults or {}))
    record.full_clean()
    record.save()
    return record


def _save_parent(store, group, first):
    data = group.common
    if group.product and first.action != "update":
        return group.product
    product = group.product
    if not product:
        vendor = default_vendor(store)
        if not vendor:
            raise InvalidRow("group", "فروشنده پیش‌فرض این فروشگاه تنظیم نشده است.")
        product = Product(
            store=store, vendor=vendor, sku=group.code, price=first.price or 0,
            product_type=Product.ProductType.VARIABLE if first.pairs else Product.ProductType.SIMPLE,
            slug=generate_unique_slug(Product, data["name"], store=store),
            status=Product.Status.DRAFT,
        )
    if data["category"]:
        product.category = group.child or group.root
    if data["brand"]:
        product.brand = group.brand
    if data["tax"]:
        product.tax_class = group.tax
    if data["status"]:
        product.status = STATUSES[data["status"]]
    if data["shipping"]:
        product.requires_shipping = data["shipping"] == "بله"
    for key in ("name", "seo_title", "seo_description"):
        if data[key]:
            setattr(product, key, data[key])
    if not first.pairs:
        if first.price is not None:
            product.price = first.price
        if first.barcode:
            product.barcode = first.barcode
        if first.weight is not None:
            product.weight_grams = first.weight
    product.full_clean()
    product.save()
    if group.specs:
        from apps.catalog.models import Specification

        existing = {normalization_key(item.label): item for item in product.specifications.all()}
        last = max((item.order for item in existing.values()), default=0)
        for label, value in group.specs:
            spec = existing.get(normalization_key(label))
            if not spec:
                last += 1
                spec = Specification(product=product, label=label, order=last)
            spec.value = value
            spec.full_clean()
            spec.save()
    return product


def _save_variant(store, product, line):
    variant = line.target
    if variant is None:
        values = {}
        for _, axis, _, value, _ in line.pairs:
            option = product.options.filter(attribute=axis, is_active=True).first()
            if option is None:
                option = product.options.filter(normalized_label=normalization_key(axis.label), is_active=True).first()
            if option is None:
                option = add_product_option(product, label=axis.label, attribute=axis)
            option_value = option.values.filter(normalized_label=normalization_key(value.label), is_active=True).first()
            if option_value is None:
                option_value = add_option_value(option, value.label, color_hex=value.color_hex,
                                                attribute_value=value if option.attribute_id else None)
            values[option.pk] = option_value.pk
        variant = add_manual_variant(product, values)
        variant.sku = line.sku
    elif line.sku:
        variant.sku = line.sku
    if line.barcode:
        variant.barcode = line.barcode
    if line.weight is not None:
        variant.weight_grams = line.weight
    if line.price is not None:
        variant.price = line.price   # independent final price in tomans; no cartesian generation or price delta
        variant.extra_price = 0
    variant.full_clean(exclude=["normalized_attribute", "normalized_value"])
    variant.save()
    return variant


def outcomes(groups, lines, *, apply=False, store=None, actor=None):
    from apps.dashboard.services.import_service import RowOutcome

    current = {}
    ordered_lines = [line for group in groups.values() for line in group.lines] if apply else lines
    result = []
    fields = {
        "sku": "sku", "barcode": "barcode", "price": "price", "stock": "stock",
        "weight_grams": "weight", "status": "status", "category": "category", "brand": "brand",
        "tax_class": "tax", "name": "name", "seo_title": "seo_title", "seo_description": "seo_description",
    }
    for line in ordered_lines:
        target = line.target
        status = RS.INVALID if line.errors else RS.VALID
        errors = list(line.errors)
        try:
            if apply and line.action != "skip":
                group = groups[line.group]
                if group.code not in current:
                    current[group.code] = _save_parent(store, group, group.lines[0])
                product = current[group.code]
                target = _save_variant(store, product, line) if line.pairs else product
                if line.stock is not None:
                    adjust_stock_manually(store=store, product=product, variant=target if line.pairs else None,
                                          new_stock=line.stock, actor=actor, note="Excel 3.3")
                status = RS.CREATED if line.action == "create" else RS.UPDATED
            elif not errors and line.action == "skip":
                status = RS.SKIPPED
        except InvalidRow as exc:
            status, errors = RS.FAILED, [str(exc)]
        except ValidationError as exc:
            status = RS.FAILED
            errors = ([error(fields.get(key, "group"), message) for key, messages in exc.message_dict.items()
                       for message in messages] if hasattr(exc, "message_dict") else [error("group", str(exc))])
        except (IntegrityError, VariantEngineError, ValueError) as exc:
            status, errors = RS.FAILED, [error("group", str(exc) if isinstance(exc, VariantEngineError)
                                              else "ثبت ردیف با وضعیت فعلی فروشگاه سازگار نیست.")]
        result.append(RowOutcome(
            row_number=line.number, source_identifier=(getattr(target, "sku", "") or line.sku or line.group),
            status=status, errors=errors,
            target_object_type=type(target).__name__ if target else "",
            target_object_id=target.pk if target else None,
            normalized_data_summary={"action": line.action, "product_name": line.name, "v33": True},
        ))
        if status == RS.FAILED:
            break
    return result


def validate(store, rows, mode):
    products, definitions, declared = plan_lists(store, rows)
    groups, lines, limit = plan(store, products, mode, declared)
    return [*outcomes(groups, lines), *(row for row, _ in definitions)], limit


def execute(store, rows, mode, actor):
    # The enclosing ImportJob transaction also covers reference definitions and stock.
    Store.objects.select_for_update().get(pk=store.pk)
    products, definitions, declared = plan_lists(store, rows)
    groups, lines, limit = plan(store, products, mode, declared)
    if limit and lines:
        lines[0].errors.append(error("group", limit))
    if any(line.errors for line in lines) or any(row.errors for row, _ in definitions):
        return [*outcomes(groups, lines), *(row for row, _ in definitions)]

    applied = []
    records = {}
    for row, additions in definitions:
        for kind, name, parent, parent_id in additions:
            try:
                records[definition_key(kind, name, parent)] = save_definition(
                    store, kind, name, records.get(parent, parent_id),
                )
            except (IntegrityError, ValidationError, ValueError):
                row.status = RS.FAILED
                columns = dict(zip(LIST_KEYS, row.normalized_data_summary["list_columns"], strict=True))
                row.errors = [list_error_at(error(kind, "ثبت تعریف با وضعیت فعلی فروشگاه سازگار نیست."), columns)]
                return [*applied, row]
        if additions:
            row.status = RS.CREATED
        applied.append(row)
    if declared:
        # Resolve the persisted reference IDs before creating products and variants.
        groups, lines, limit = plan(store, products, mode, set())
        if limit and lines:
            lines[0].errors.append(error("group", limit))
        if any(line.errors for line in lines):
            return [*applied, *outcomes(groups, lines)]
    return [*applied, *outcomes(groups, lines, apply=True, store=store, actor=actor)]
