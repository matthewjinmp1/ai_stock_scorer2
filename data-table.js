function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function uniqueKnownKeys(keys, knownKeys) {
  const valid = Array.isArray(keys)
    ? [...new Set(keys.filter((key) => knownKeys.includes(key)))]
    : [];
  return [...valid, ...knownKeys.filter((key) => !valid.includes(key))];
}

export class DataTable {
  constructor({
    table,
    body,
    columns,
    selector = null,
    selectorOptions = null,
    resetColumnsButton = null,
    resetOrderButton = null,
    storageKey = null,
    orderStorageKey = null,
    loadPreferences = null,
    savePreferences = null,
    onSort = null,
    onRowClick = null,
    statusElement = null,
  }) {
    this.table = table;
    this.body = body || table?.querySelector("tbody");
    this.headRow = table?.querySelector("thead tr");
    this.columns = columns;
    this.columnMap = new Map(columns.map((column) => [column.key, column]));
    this.selector = selector;
    this.selectorOptions = selectorOptions;
    this.resetColumnsButton = resetColumnsButton;
    this.resetOrderButton = resetOrderButton;
    this.storageKey = storageKey;
    this.orderStorageKey = orderStorageKey;
    this.loadPreferences = loadPreferences;
    this.savePreferences = savePreferences;
    this.onSort = onSort;
    this.onRowClick = onRowClick;
    this.statusElement = statusElement;
    this.scope = "default";
    this.context = {};
    this.rows = [];
    this.emptyMessage = "No rows.";
    this.sortState = null;
    this.preferenceState = new Map();
    this.saveTimers = new Map();
    this.bindEvents();
  }

  allKeys() {
    return this.columns.map((column) => column.key);
  }

  defaultVisibleKeys() {
    return this.columns
      .filter((column) => column.defaultVisible !== false)
      .map((column) => column.key);
  }

  storageName(base, scope = this.scope) {
    if (!base) return null;
    if (typeof base === "function") return base(scope);
    return scope === "default" ? base : `${base}:${scope}`;
  }

  readLocalState(scope) {
    const allKeys = this.allKeys();
    let visible = this.defaultVisibleKeys();
    let order = [...allKeys];
    try {
      const stored = JSON.parse(localStorage.getItem(this.storageName(this.storageKey, scope)));
      const valid = Array.isArray(stored) ? stored.filter((key) => allKeys.includes(key)) : [];
      if (valid.length) visible = valid;
    } catch (_error) {
      // Defaults remain available when storage is blocked or malformed.
    }
    try {
      order = uniqueKnownKeys(
        JSON.parse(localStorage.getItem(this.storageName(this.orderStorageKey, scope))),
        allKeys
      );
    } catch (_error) {
      // Defaults remain available when storage is blocked or malformed.
    }
    return { visible: new Set(visible), order };
  }

  state(scope = this.scope) {
    if (!this.preferenceState.has(scope)) {
      this.preferenceState.set(scope, this.readLocalState(scope));
    }
    return this.preferenceState.get(scope);
  }

  availableColumns() {
    return this.columns.filter(
      (column) => !column.available || column.available(this.context, this.scope)
    );
  }

  visibleColumns() {
    const state = this.state();
    const availableColumns = this.availableColumns();
    const available = new Set(availableColumns.map((column) => column.key));
    const ordered = state.order
      .filter((key) => available.has(key) && state.visible.has(key))
      .map((key) => this.columnMap.get(key));
    const starts = availableColumns.filter((column) => column.pinned === "start");
    const ends = availableColumns.filter((column) => column.pinned === "end");
    return [
      ...starts,
      ...ordered.filter((column) => !column.pinned),
      ...ends,
    ];
  }

  configurableColumns() {
    const available = new Set(this.availableColumns().map((column) => column.key));
    return this.state().order
      .filter((key) => available.has(key))
      .map((key) => this.columnMap.get(key))
      .filter((column) => column.configurable !== false);
  }

  async initialize(scope = "default", context = {}) {
    this.scope = scope;
    this.context = context;
    const state = this.state(scope);
    if (this.loadPreferences) {
      try {
        const saved = await this.loadPreferences(scope);
        const allKeys = this.allKeys();
        const visible = Array.isArray(saved?.columns)
          ? saved.columns.filter((key) => allKeys.includes(key))
          : [];
        if (visible.length) state.visible = new Set(visible);
        if (Array.isArray(saved?.order)) state.order = uniqueKnownKeys(saved.order, allKeys);
        this.saveLocalState(scope);
      } catch (_error) {
        // Local preferences remain the fallback when the server is unavailable.
      }
    }
    this.render();
    return this;
  }

  async setScope(scope, context = this.context) {
    this.scope = scope;
    this.context = context;
    if (!this.preferenceState.has(scope)) await this.initialize(scope, context);
    else this.render();
  }

  setContext(context) {
    this.context = context;
    this.render();
  }

  setSortState(sortState) {
    this.sortState = sortState;
    this.renderHeader();
  }

  setRows(rows, { emptyMessage = "No rows." } = {}) {
    this.rows = Array.isArray(rows) ? rows : [];
    this.emptyMessage = emptyMessage;
    this.render();
  }

  setLoading(message = "Loading...") {
    this.rows = [];
    this.emptyMessage = message;
    this.render();
  }

  render() {
    this.renderHeader();
    this.renderBody();
    this.renderSelector();
  }

  renderHeader() {
    if (!this.headRow) return;
    this.headRow.innerHTML = this.visibleColumns()
      .map((column) => {
        const sortKey = column.sortKey;
        const active = sortKey && this.sortState?.key === sortKey;
        const ariaSort = active
          ? this.sortState.direction === "asc" ? "ascending" : "descending"
          : "none";
        const label = escapeHtml(column.label);
        const content = sortKey
          ? `<button class="sort-button${active ? " is-active" : ""}" type="button" data-table-sort="${escapeHtml(sortKey)}">${label}</button>`
          : label;
        return `<th data-table-column="${escapeHtml(column.key)}"${sortKey ? ` aria-sort="${ariaSort}"` : ""}${column.headerClass ? ` class="${escapeHtml(column.headerClass)}"` : ""}>${content}</th>`;
      })
      .join("");
  }

  renderBody() {
    if (!this.body) return;
    const columns = this.visibleColumns();
    if (!this.rows.length) {
      this.body.innerHTML = `<tr><td data-empty-results colspan="${Math.max(1, columns.length)}">${escapeHtml(this.emptyMessage)}</td></tr>`;
      return;
    }
    this.body.innerHTML = this.rows
      .map((row, index) => {
        const rowClass = typeof this.context.rowClass === "function"
          ? this.context.rowClass(row, index)
          : this.context.rowClass || "";
        const attrs = typeof this.context.rowAttributes === "function"
          ? this.context.rowAttributes(row, index)
          : "";
        const cells = columns.map((column) => {
          const rendered = column.render ? column.render(row, index, this.context) : row[column.key];
          const cellClass = typeof column.cellClass === "function"
            ? column.cellClass(row, index, this.context)
            : column.cellClass || "";
          return `<td data-label="${escapeHtml(column.label)}" data-table-column="${escapeHtml(column.key)}"${cellClass ? ` class="${escapeHtml(cellClass)}"` : ""}>${rendered ?? ""}</td>`;
        }).join("");
        return `<tr${rowClass ? ` class="${escapeHtml(rowClass)}"` : ""}${attrs ? ` ${attrs}` : ""}>${cells}</tr>`;
      })
      .join("");
  }

  renderSelector() {
    if (!this.selectorOptions) return;
    const state = this.state();
    const columns = this.configurableColumns();
    this.selectorOptions.innerHTML = columns.map((column, index) => `
      <div class="column-selector-row">
        <label><input type="checkbox" data-table-column-toggle="${escapeHtml(column.key)}" ${state.visible.has(column.key) ? "checked" : ""} /> ${escapeHtml(column.label)}</label>
        <div class="column-order-controls">
          <button class="column-order-button" type="button" data-table-move-column="${escapeHtml(column.key)}" data-table-move-direction="-1" title="Move earlier" aria-label="Move ${escapeHtml(column.label)} earlier" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="column-order-button" type="button" data-table-move-column="${escapeHtml(column.key)}" data-table-move-direction="1" title="Move later" aria-label="Move ${escapeHtml(column.label)} later" ${index === columns.length - 1 ? "disabled" : ""}>↓</button>
        </div>
      </div>`).join("");
  }

  saveLocalState(scope = this.scope) {
    const state = this.state(scope);
    try {
      localStorage.setItem(this.storageName(this.storageKey, scope), JSON.stringify([...state.visible]));
      localStorage.setItem(this.storageName(this.orderStorageKey, scope), JSON.stringify(state.order));
    } catch (_error) {
      // In-memory preferences still work for this page.
    }
  }

  persistState() {
    const scope = this.scope;
    const state = this.state(scope);
    this.saveLocalState(scope);
    if (!this.savePreferences) return;
    if (this.saveTimers.has(scope)) clearTimeout(this.saveTimers.get(scope));
    this.saveTimers.set(scope, setTimeout(() => {
      const configurableKeys = new Set(
        this.columns.filter((column) => column.configurable !== false).map((column) => column.key)
      );
      this.savePreferences(scope, {
        columns: [...state.visible].filter((key) => configurableKeys.has(key)),
        order: state.order.filter((key) => configurableKeys.has(key)),
      })
        .catch(() => {});
    }, 200));
  }

  toggleColumn(key, checked) {
    const state = this.state();
    if (checked) state.visible.add(key);
    else state.visible.delete(key);
    const visibleConfigurable = this.configurableColumns().filter((column) => state.visible.has(column.key));
    if (!visibleConfigurable.length) {
      state.visible.add(key);
      if (this.statusElement) this.statusElement.textContent = "Keep at least one table column visible.";
    }
    this.persistState();
    this.render();
  }

  moveColumn(key, direction) {
    const state = this.state();
    const movable = this.configurableColumns().map((column) => column.key);
    const index = movable.indexOf(key);
    const neighbor = movable[index + direction];
    if (index < 0 || !neighbor) return;
    const sourceIndex = state.order.indexOf(key);
    const targetIndex = state.order.indexOf(neighbor);
    [state.order[sourceIndex], state.order[targetIndex]] = [state.order[targetIndex], state.order[sourceIndex]];
    this.persistState();
    this.render();
  }

  resetColumns() {
    this.state().visible = new Set(this.defaultVisibleKeys());
    this.persistState();
    this.render();
  }

  resetOrder() {
    this.state().order = this.allKeys();
    this.persistState();
    this.render();
  }

  bindEvents() {
    this.headRow?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-table-sort]");
      if (button && this.onSort) this.onSort(button.dataset.tableSort);
    });
    this.body?.addEventListener("click", (event) => {
      if (!this.onRowClick || event.target.closest("button, a, input, select, textarea")) return;
      const row = event.target.closest("tr");
      if (!row) return;
      const index = [...this.body.children].indexOf(row);
      if (index >= 0 && this.rows[index]) this.onRowClick(this.rows[index], event);
    });
    this.selector?.addEventListener("change", (event) => {
      const input = event.target.closest("[data-table-column-toggle]");
      if (input) this.toggleColumn(input.dataset.tableColumnToggle, input.checked);
    });
    this.selector?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-table-move-column]");
      if (button) this.moveColumn(button.dataset.tableMoveColumn, Number(button.dataset.tableMoveDirection));
    });
    this.resetColumnsButton?.addEventListener("click", () => this.resetColumns());
    this.resetOrderButton?.addEventListener("click", () => this.resetOrder());
    document.addEventListener("click", (event) => {
      if (this.selector?.open && !this.selector.contains(event.target)) this.selector.open = false;
    });
  }
}

export function bindTablePagination({ previousButton, nextButton, statusElement, onPageChange }) {
  let page = 1;
  let totalPages = 1;
  previousButton?.addEventListener("click", () => {
    if (page > 1) onPageChange(page - 1);
  });
  nextButton?.addEventListener("click", () => {
    if (page < totalPages) onPageChange(page + 1);
  });
  return ({ page: nextPage = 1, totalPages: nextTotalPages = 1, label = null } = {}) => {
    page = nextPage;
    totalPages = Math.max(1, nextTotalPages);
    if (previousButton) previousButton.disabled = page <= 1;
    if (nextButton) nextButton.disabled = page >= totalPages;
    if (statusElement) statusElement.textContent = label || `Page ${page.toLocaleString()} of ${totalPages.toLocaleString()}`;
  };
}
