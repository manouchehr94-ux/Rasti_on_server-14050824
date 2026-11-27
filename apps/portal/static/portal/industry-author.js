(() => {
  const form = document.getElementById('industry-author');
  if (!form) return;
  const raw = document.getElementById('definition');
  const advanced = document.getElementById('industry-advanced');
  const error = document.getElementById('industry-form-error');
  const owner = document.getElementById('industry-fields');
  const sectors = JSON.parse(document.getElementById('industry-sectors').textContent);
  const choices = (name) => JSON.parse(document.getElementById('industry-' + name).textContent);
  let draft;
  try { draft = JSON.parse(raw.value); } catch { advanced.open = true; return; }
  if (!draft || typeof draft !== 'object' || Array.isArray(draft)) { advanced.open = true; return; }
  const sections = {
    categories: ['دسته', [
      ['name', 'نام دسته', 'text', true], ['parent_code', 'دستهٔ والد', 'category'],
      ['icon', 'آیکون'], ['display_order', 'ترتیب', 'number'],
    ]],
    attributes: ['ویژگی', [
      ['label', 'نام ویژگی', 'text', true], ['data_type', 'نوع داده', 'types'],
      ['display_type', 'شیوهٔ نمایش', 'displays'], ['unit', 'واحد'],
      ['is_variant_axis', 'محور تنوع', 'checkbox'], ['display_order', 'ترتیب', 'number'],
    ]],
    mappings: ['نگاشت', [
      ['category_code', 'دسته', 'category', true], ['attribute_code', 'ویژگی', 'attribute', true],
      ['group', 'عنوان گروه'], ['group_order', 'ترتیب گروه', 'number'],
      ['display_order', 'ترتیب ویژگی', 'number'], ['is_required', 'الزامی', 'checkbox'],
      ['is_filterable', 'قابل فیلتر', 'checkbox'], ['is_comparable', 'قابل مقایسه', 'checkbox'],
      ['is_searchable', 'قابل جست‌وجو', 'checkbox'], ['help_text', 'راهنما'], ['placeholder', 'متن نمونه'],
    ]],
  };
  const valueFields = [['label', 'برچسب مقدار', 'text', true], ['value', 'مقدار داخلی'],
    ['color_hex', 'رنگ (کد Hex)'], ['display_order', 'ترتیب', 'number']];
  const header = [['name', 'نام صنف', 'text', true], ['description', 'توضیحات', 'textarea'],
    ['sector', 'رسته', 'sectors'], ['icon', 'آیکون'], ['display_order', 'ترتیب نمایش', 'number']];
  for (const key of Object.keys(sections)) {
    if (!Array.isArray(draft[key]) || draft[key].some((row) => !row || typeof row !== 'object')) {
      advanced.open = true;
      return;
    }
  }

  function options(type, row) {
    if (type === 'category') return draft.categories.filter((item) => item !== row)
      .map((item) => [item.code, item.name || 'دستهٔ تازه']);
    if (type === 'attribute') return draft.attributes.map((item) => [item.code, item.label || 'ویژگی تازه']);
    return type === 'sectors' ? sectors : choices(type);
  }

  function field(parent, row, [key, label, type = 'text', required = false]) {
    const wrapper = document.createElement('label');
    wrapper.className = 'pa-field';
    wrapper.append(document.createTextNode(label));
    const select = ['category', 'attribute', 'sectors', 'types', 'displays'].includes(type);
    const el = document.createElement(select ? 'select' : type === 'textarea' ? type : 'input');
    if (select) {
      const available = options(type, row);
      const blank = type === 'category' || type === 'attribute' || type === 'displays';
      if (blank && !available.some(([value]) => value === '')) el.add(new Option('—', ''));
      available.forEach(([value, name]) => el.add(new Option(name, value)));
      if (row[key] == null && !blank) row[key] = type === 'sectors' ? 'other' : el.value;
    } else if (type !== 'textarea') {
      el.type = type;
      if (type === 'number') { el.min = '0'; el.step = '1'; }
    }
    if (type === 'checkbox') el.checked = Boolean(row[key]);
    else el.value = row[key] ?? (type === 'number' ? '0' : '');
    el.addEventListener('input', () => {
      row[key] = type === 'checkbox' ? el.checked : type === 'number' ? Number(el.value)
        : key === 'parent_code' && !el.value ? null : el.value;
    });
    if (key === 'name' || key === 'label') el.addEventListener('change', refreshReferences);
    el.required = required;
    el.disabled = advanced.open;
    wrapper.append(el);
    parent.append(wrapper);
    if (type === 'category' || type === 'attribute') {
      el.dataset.reference = type;
      el.industryRow = row;
      el.industryKey = key;
    }
  }

  function refreshReferences() {
    owner.querySelectorAll('[data-reference]').forEach((select) => {
      const row = select.industryRow;
      const key = select.industryKey;
      const current = row[key] || '';
      const found = options(select.dataset.reference, row);
      select.replaceChildren(new Option('—', ''), ...found.map(([value, label]) => new Option(label, value)));
      select.value = current;
      if (current && !select.value) row[key] = key === 'parent_code' ? null : '';
    });
  }

  function button(label, click) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'pa-btn';
    el.textContent = label;
    el.addEventListener('click', click);
    return el;
  }

  function renderRows(parent, items, label, fields, valuesAllowed = false) {
    parent.replaceChildren();
    items.forEach((row, index) => {
      const card = document.createElement('fieldset');
      card.className = 'pa-card pa-cardbody';
      const title = document.createElement('legend');
      title.textContent = label + ' ' + (index + 1);
      card.append(title);
      fields.forEach((desc) => field(card, row, desc));
      if (valuesAllowed) {
        row.values ||= [];
        const values = document.createElement('div');
        card.append(values);
        renderRows(values, row.values, 'مقدار', valueFields);
        card.append(button('افزودن مقدار', () => {
          row.values.push({label: ''});
          renderRows(values, row.values, 'مقدار', valueFields);
          values.lastElementChild.querySelector('input').focus();
        }));
      }
      card.append(button('حذف ' + label + ' از این نسخه', () => {
        if (!window.confirm('این مورد از نسخهٔ جدید حذف شود؟')) return;
        items.splice(index, 1);
        if (row.code) {
          const belongsToCategory = items === draft.categories;
          const reference = belongsToCategory ? 'category_code' : 'attribute_code';
          draft.mappings = draft.mappings.filter((item) => item[reference] !== row.code);
          draft.recommendations = (draft.recommendations || []).filter((item) => item[reference] !== row.code);
          if (belongsToCategory) draft.categories.forEach((item) => {
            if (item.parent_code === row.code) item.parent_code = null;
          });
          render();
        } else renderRows(parent, items, label, fields, valuesAllowed);
      }));
      parent.append(card);
    });
  }

  function render() {
    const fields = owner.querySelector('[data-fields="header"]');
    fields.replaceChildren();
    header.forEach((desc) => field(fields, draft, desc));
    for (const [key, [label, config]] of Object.entries(sections)) {
      renderRows(owner.querySelector('[data-list="' + key + '"]'), draft[key], label, config, key === 'attributes');
    }
  }

  owner.querySelectorAll('[data-add]').forEach((add) => add.addEventListener('click', () => {
    const key = add.dataset.add;
    const row = key === 'attributes' ? {label: '', data_type: 'text', values: []} : key === 'categories' ? {name: ''} : {};
    if (key !== 'mappings') row.code = (key === 'attributes' ? 'attr-' : 'cat-') + crypto.randomUUID();
    draft[key].push(row);
    render();
    owner.querySelector('[data-list="' + key + '"]').lastElementChild.querySelector('input,select').focus();
  }));
  advanced.addEventListener('toggle', () => {
    if (advanced.open) raw.value = JSON.stringify(draft, null, 2);
    else {
      try {
        const updated = JSON.parse(raw.value);
        if (updated && Object.keys(sections).every((key) => Array.isArray(updated[key]))) draft = updated;
      } catch { /* Keep the last valid form state. */ }
      render();
    }
    owner.querySelectorAll('input,select,textarea,button').forEach((el) => { el.disabled = advanced.open; });
  });
  form.addEventListener('submit', (event) => {
    if (advanced.open) return;
    if (!draft.slug) {
      const slug = String(draft.name || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      draft.slug = slug.slice(0, 40) || 'industry-' + crypto.randomUUID();
    }
    if (draft.categories.some((item) => item.parent_code && !draft.categories.some((parent) => parent.code === item.parent_code))) {
      event.preventDefault();
      error.textContent = 'دستهٔ والد را از میان دسته‌های این نسخه انتخاب کنید.';
      error.hidden = false;
      return;
    }
    raw.value = JSON.stringify(draft);
  });
  raw.required = false;
  owner.hidden = false;
  render();
})();
