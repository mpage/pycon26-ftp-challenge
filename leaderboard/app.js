const REFRESH_INTERVAL_MS = 30_000;
const RELATIVE_TIME_INTERVAL_MS = 1_000;

const state = {
  baselineTime: null,
  entries: [],
  lastUpdated: null,
  sortKey: "rank",
  sortDirection: "asc",
  errorMessage: "",
};

const elements = {
  lastUpdated: document.getElementById("last-updated"),
  statusMessage: document.getElementById("status-message"),
  errorBanner: document.getElementById("error-banner"),
  table: document.getElementById("leaderboard-table"),
  tbody: document.getElementById("leaderboard-body"),
  emptyState: document.getElementById("empty-state"),
  sortButtons: Array.from(document.querySelectorAll(".sort-button")),
};

function resolveLeaderboardUrl() {
  const url = new URL("../scores/leaderboard.json", window.location.href);
  url.searchParams.set("t", Date.now().toString());
  return url.toString();
}

function normaliseEntries(entries) {
  return (Array.isArray(entries) ? entries : [])
    .filter((entry) => entry && typeof entry.median_time_seconds === "number" && entry.status === "valid")
    .map((entry) => ({
      username: entry.username || "unknown",
      displayName: entry.display_name || "",
      prNumber: entry.pr_number ?? null,
      prUrl: entry.pr_url || "#",
      mergedAt: entry.merged_at || null,
      medianTimeSeconds: entry.median_time_seconds,
      speedup: entry.speedup || "",
      speedupValue: Number.isFinite(Number.parseFloat(entry.speedup))
        ? Number.parseFloat(entry.speedup)
        : null,
      numRuns: entry.num_runs ?? null,
      isMeta: entry.is_meta === true,
    }));
}

function compareValues(a, b, sortKey, sortDirection) {
  const direction = sortDirection === "asc" ? 1 : -1;
  let valueA;
  let valueB;

  switch (sortKey) {
    case "rank":
      valueA = b.speedupValue ?? Number.NEGATIVE_INFINITY;
      valueB = a.speedupValue ?? Number.NEGATIVE_INFINITY;
      break;
    case "username":
      valueA = (a.displayName || a.username).toLowerCase();
      valueB = (b.displayName || b.username).toLowerCase();
      break;
    case "speedup":
      valueA = a.speedupValue ?? Number.NEGATIVE_INFINITY;
      valueB = b.speedupValue ?? Number.NEGATIVE_INFINITY;
      break;
    case "merged_at":
      valueA = a.mergedAt ? Date.parse(a.mergedAt) : 0;
      valueB = b.mergedAt ? Date.parse(b.mergedAt) : 0;
      break;
    default:
      valueA = b.speedupValue ?? Number.NEGATIVE_INFINITY;
      valueB = a.speedupValue ?? Number.NEGATIVE_INFINITY;
      break;
  }

  if (valueA < valueB) {
    return -1 * direction;
  }

  if (valueA > valueB) {
    return 1 * direction;
  }

  return a.username.localeCompare(b.username);
}

function getSortedEntries() {
  return [...state.entries].sort((a, b) => compareValues(a, b, state.sortKey, state.sortDirection));
}

function formatSeconds(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }

  return `${value.toFixed(2)}s`;
}

function formatSpeedup(entry) {
  if (entry.speedupValue !== null) {
    return `${entry.speedupValue.toFixed(2)}x`;
  }

  if (typeof state.baselineTime === "number" && entry.medianTimeSeconds > 0) {
    return `${(state.baselineTime / entry.medianTimeSeconds).toFixed(2)}x`;
  }

  return "--";
}

function formatRelativeTime(isoString) {
  if (!isoString) {
    return "Unknown";
  }

  const target = Date.parse(isoString);
  if (Number.isNaN(target)) {
    return "Unknown";
  }

  const deltaSeconds = Math.max(0, Math.floor((Date.now() - target) / 1000));
  if (deltaSeconds < 5) {
    return "just now";
  }
  if (deltaSeconds < 60) {
    return `${deltaSeconds} sec ago`;
  }

  const deltaMinutes = Math.floor(deltaSeconds / 60);
  if (deltaMinutes < 60) {
    return `${deltaMinutes} min ago`;
  }

  const deltaHours = Math.floor(deltaMinutes / 60);
  if (deltaHours < 24) {
    return `${deltaHours} hr ago`;
  }

  const deltaDays = Math.floor(deltaHours / 24);
  return `${deltaDays} day${deltaDays === 1 ? "" : "s"} ago`;
}

function formatTimestamp(isoString) {
  if (!isoString) {
    return "--";
  }

  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function getTrophyMarkup(rank) {
  if (rank === 1) {
    return '<span class="trophy gold" aria-hidden="true">🏆</span>';
  }
  if (rank === 2) {
    return '<span class="trophy silver" aria-hidden="true">🥈</span>';
  }
  if (rank === 3) {
    return '<span class="trophy bronze" aria-hidden="true">🥉</span>';
  }
  return "";
}

function updateSortButtons() {
  elements.sortButtons.forEach((button) => {
    const isActive = button.dataset.sortKey === state.sortKey;
    if (isActive) {
      button.dataset.sortDirection = state.sortDirection;
      button.setAttribute("aria-sort", state.sortDirection === "asc" ? "ascending" : "descending");
    } else {
      delete button.dataset.sortDirection;
      button.removeAttribute("aria-sort");
    }
  });
}

function renderRows(sortedEntries) {
  const firstRects = new Map(
    Array.from(elements.tbody.children, (row) => [row.dataset.rowKey, row.getBoundingClientRect()])
  );
  const fragment = document.createDocumentFragment();

  sortedEntries.forEach((entry, index) => {
    const rank = index + 1;
    const row = document.createElement("tr");
    row.dataset.rowKey = entry.username;
    row.innerHTML = `
      <td>
        <div class="rank-cell">
          <span>${rank}</span>
          ${getTrophyMarkup(rank)}
        </div>
      </td>
      <td>
      <div class="pr-cell">
        <a class="username-link" href="${entry.prUrl}" target="_blank" rel="noreferrer">${entry.username}</a>
        ${entry.isMeta ? '<span class="meta-badge" title="Meta employee">META</span>' : ""}
      </div>
        ${entry.displayName ? `<span class="display-name">${entry.displayName}</span>` : ""}
      </td>
      <td class="mono">${formatSpeedup(entry)}</td>
      <td>
        <div class="submitted-cell">
          <span>${formatRelativeTime(entry.mergedAt)}</span>
          <span>${formatTimestamp(entry.mergedAt)}</span>
        </div>
      </td>
    `;
    fragment.appendChild(row);
  });

  elements.tbody.replaceChildren(fragment);

  requestAnimationFrame(() => {
    Array.from(elements.tbody.children).forEach((row) => {
      const startRect = firstRects.get(row.dataset.rowKey);
      if (!startRect) {
        row.animate(
          [
            { opacity: 0, transform: "translateY(14px)" },
            { opacity: 1, transform: "translateY(0)" },
          ],
          { duration: 420, easing: "ease-out" }
        );
        return;
      }

      const endRect = row.getBoundingClientRect();
      const deltaY = startRect.top - endRect.top;
      if (deltaY !== 0) {
        row.animate(
          [
            { transform: `translateY(${deltaY}px)` },
            { transform: "translateY(0)" },
          ],
          { duration: 650, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
        );
      }
    });
  });
}

function render() {
  const sortedEntries = getSortedEntries();
  const hasEntries = sortedEntries.length > 0;

  elements.statusMessage.textContent = hasEntries
    ? `${sortedEntries.length} participant${sortedEntries.length === 1 ? "" : "s"} ranked`
    : "Waiting for the first valid submission";
  elements.lastUpdated.textContent = state.lastUpdated
    ? `Updated ${formatRelativeTime(state.lastUpdated)} (${formatTimestamp(state.lastUpdated)})`
    : "";

  if (state.errorMessage) {
    elements.errorBanner.hidden = false;
    elements.errorBanner.textContent = state.errorMessage;
  } else {
    elements.errorBanner.hidden = true;
    elements.errorBanner.textContent = "";
  }

  elements.table.hidden = !hasEntries;
  elements.emptyState.hidden = hasEntries;

  if (hasEntries) {
    renderRows(sortedEntries);
  } else {
    elements.tbody.replaceChildren();
  }
  updateSortButtons();
}

async function fetchLeaderboard() {
  try {
    const response = await fetch(resolveLeaderboardUrl(), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    state.baselineTime = typeof payload.baseline_time === "number" ? payload.baseline_time : null;
    state.lastUpdated = payload.last_updated || new Date().toISOString();
    state.entries = normaliseEntries(payload.entries);
    state.errorMessage = "";
    render();
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unknown error";
    state.errorMessage = `Unable to refresh leaderboard right now (${detail}). Retrying automatically.`;
    render();
  }
}

function handleSortClick(event) {
  const button = event.currentTarget;
  const { sortKey } = button.dataset;
  if (!sortKey) {
    return;
  }

  if (state.sortKey === sortKey) {
    state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
  } else {
    state.sortKey = sortKey;
    state.sortDirection = sortKey === "merged_at" ? "desc" : "asc";
  }

  render();
}

function startTimers() {
  window.setInterval(fetchLeaderboard, REFRESH_INTERVAL_MS);
  window.setInterval(() => {
    if (state.lastUpdated || state.entries.length > 0) {
      render();
    }
  }, RELATIVE_TIME_INTERVAL_MS);
}

function initialise() {
  elements.sortButtons.forEach((button) => {
    button.addEventListener("click", handleSortClick);
  });

  render();
  fetchLeaderboard();
  startTimers();
}

initialise();
