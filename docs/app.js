const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const DAY_LABELS = {
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
  sunday: "Sun",
};
const FAVORITES_KEY = "restaurant-deals:favorites";
const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js";
const MAPLIBRE_CSS_URL = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css";
const DEFAULT_TYPES = ["food", "drink"];
const DEFAULT_KINDS = ["happy_hour", "other"];
const NEW_DEAL_DAYS = 7;
const CHAIN_PATTERN = /\b(?:applebee'?s|arby'?s|baja fresh|blaze pizza|burger king|carl'?s jr|chick-fil-a|chipotle|denny'?s|domino'?s|el pollo loco|five guys|habit burger|ihop|in-n-out|jack in the box|jersey mike'?s|kfc|little caesars|mcdonald'?s|panda express|panera|papa john'?s|pizza hut|popeyes|raising cane'?s|round table pizza|rubio'?s|shake shack|sonic|starbucks|subway|taco bell|wendy'?s|wienerschnitzel|wingstop)\b/i;

const state = {
  payload: null,
  restaurants: null,
  project: null,
  query: "",
  restaurant: "",
  cities: new Set(),
  day: "today",
  status: "active",
  types: new Set(DEFAULT_TYPES),
  kinds: new Set(DEFAULT_KINDS),
  includeChains: true,
  sort: "alpha",
  radius: "",
  availableNow: false,
  favoritesOnly: false,
  newOnly: false,
  favorites: loadFavorites(),
  view: "list",
  spot: "",
  userLocation: null,
  locationMessage: "",
  map: null,
  mapMarkers: [],
};

const dealsEl = document.querySelector("#deals");
const statsEl = document.querySelector("#stats");
const metaEl = document.querySelector("#meta");
const sourcesEl = document.querySelector("#sources");
const searchEl = document.querySelector("#search");
const restaurantEl = document.querySelector("#restaurant");
const cityPickerEl = document.querySelector("#city-picker");
const citySummaryEl = document.querySelector("#city-summary");
const cityOptionsEl = document.querySelector("#city-options");
const dayEl = document.querySelector("#day");
const offerPickerEl = document.querySelector("#offer-picker");
const offerSummaryEl = document.querySelector("#offer-summary");
const offerOptionsEl = document.querySelector("#offer-options");
const radiusEl = document.querySelector("#radius");
const sortMenuEl = document.querySelector("#sort-menu");
const sortLabelEl = document.querySelector("#sort-label");
const filtersEl = document.querySelector("#filters");
const filterToggleEl = document.querySelector("#filter-toggle");
const filterCountEl = document.querySelector("#filter-count");
const filterSummaryEl = document.querySelector("#filter-summary");
const themeToggleEl = document.querySelector("#theme-toggle");
const coverageSummaryEl = document.querySelector("#coverage-summary");
const coverageCitiesEl = document.querySelector("#coverage-cities");
const coverageMetricsEl = document.querySelector("#coverage-metrics");
const costNoteEl = document.querySelector("#cost-note");
const roadmapListEl = document.querySelector("#roadmap-list");
const availableNowEl = document.querySelector("#available-now");
const favoritesOnlyEl = document.querySelector("#favorites-only");
const newOnlyEl = document.querySelector("#new-only");
const listViewEl = document.querySelector("#list-view");
const mapViewEl = document.querySelector("#map-view");
const shareViewEl = document.querySelector("#share-view");
const mapPanelEl = document.querySelector("#map-panel");
const mapMessageEl = document.querySelector("#map-message");
const toastEl = document.querySelector("#toast");
const connectionStatusEl = document.querySelector("#connection-status");
const installAppEl = document.querySelector("#install-app");

let mapLibraryPromise = null;
let toastTimer = null;
let installPrompt = null;
let cachedDataUsed = false;

dayEl.value = "today";

function todayKey() {
  return DAYS[new Date().getDay() === 0 ? 6 : new Date().getDay() - 1];
}

function selectedDay() {
  return state.day === "today" ? todayKey() : state.day;
}

function loadFavorites() {
  try {
    const saved = JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]");
    return new Set(Array.isArray(saved) ? saved : []);
  } catch (_) {
    return new Set();
  }
}

function saveFavorites() {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...state.favorites]));
    return true;
  } catch (_) {
    return false;
  }
}

function showToast(message) {
  clearTimeout(toastTimer);
  toastEl.textContent = message;
  toastEl.hidden = false;
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 3200);
}

function renderConnectionStatus() {
  const offline = !navigator.onLine || cachedDataUsed;
  connectionStatusEl.hidden = !offline;
  if (!offline) return;
  const savedAt = state.payload?.generated_at ? ` from ${formatDate(state.payload.generated_at)}` : "";
  connectionStatusEl.textContent = `Offline · showing saved deals${savedAt}`;
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data?.type !== "OFFLINE_DATA_USED") return;
    cachedDataUsed = true;
    renderConnectionStatus();
  });
  try {
    await navigator.serviceWorker.register("service-worker.js");
  } catch (_) {
    // Offline support is progressive; the live site still works without it.
  }
}

function trackEvent(name, data = {}) {
  if (window.umami?.track) window.umami.track(name, data);
}

function renderFilterSummary() {
  const labels = [];
  labels.push(state.day === "today" ? "Today" : state.day ? DAY_LABELS[state.day] : "Any day");
  const cityLabel = state.cities.size === 1 ? [...state.cities][0] : state.cities.size ? `${state.cities.size} cities` : "";
  labels.push(state.restaurant || cityLabel || "All restaurants");

  const activeCount = [
    state.query,
    state.restaurant,
    state.cities.size ? "cities" : "",
    state.day !== "today" ? state.day || "any" : "",
    state.types.size !== DEFAULT_TYPES.length ? "types" : "",
    state.kinds.size !== DEFAULT_KINDS.length ? "offers" : "",
    !state.includeChains ? "chains" : "",
    state.radius ? "radius" : "",
    state.availableNow ? "now" : "",
    state.favoritesOnly ? "favorites" : "",
    state.newOnly ? "new" : "",
  ].filter(Boolean).length;

  filterSummaryEl.textContent = labels.join(" · ");
  filterCountEl.textContent = activeCount;
  filterCountEl.hidden = activeCount === 0;
}

function formatDate(value) {
  if (!value) return "never";
  const formatted = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
  }).format(new Date(value));
  return `${formatted} PT`;
}

function firstSeenTime(deal) {
  const value = new Date(deal.first_seen || "").getTime();
  return Number.isFinite(value) ? value : 0;
}

function isNewDeal(deal) {
  const discovered = firstSeenTime(deal);
  const age = Date.now() - discovered;
  return discovered > 0 && age >= -86400000 && age <= NEW_DEAL_DAYS * 86400000;
}

function normalizeScheduleText(value = "") {
  let text = String(value);
  for (const [day, label] of Object.entries(DAY_LABELS)) {
    text = text.replace(new RegExp(`\\b${day}\\b`, "gi"), label);
  }
  text = text
    .replace(/\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b/gi, (_, hour, minute, period) => {
      const minutes = minute && minute !== "00" ? `:${minute}` : "";
      return `${Number(hour)}${minutes}${period.toLowerCase()}m`;
    })
    .replace(/(\d(?:am|pm))\s+(?:to|[-–—])\s+(?=\d)/gi, "$1-")
    .replace(/(\d:\d{2}(?:am|pm))\s+(?:to|[-–—])\s+(?=\d)/gi, "$1-")
    .replace(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*[-–—]\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b/g, "$1-$2")
    .replace(/\s+([,;])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
  return text;
}

function scheduleSignals(value = "") {
  const normalized = normalizeScheduleText(value).toLowerCase();
  return new Set([
    ...(normalized.match(/\b(?:mon|tue|wed|thu|fri|sat|sun)\b/g) || []),
    ...(normalized.match(/\b\d{1,2}(?::\d{2})?(?:am|pm)\b/g) || []),
    ...(normalized.match(/\b(?:all day|every day|daily)\b/g) || []),
  ]);
}

function isValidityRedundant(deal, visibleText) {
  const validity = normalizeScheduleText(deal.validity || "");
  if (!validity || /^check source$/i.test(validity)) return true;
  const signals = scheduleSignals(validity);
  if (!signals.size) return false;
  const visibleSignals = scheduleSignals(visibleText);
  return [...signals].every((signal) => visibleSignals.has(signal));
}

function tagLabel(tag) {
  return tag
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function categoryLabel(categories = []) {
  if (categories.includes("food") && categories.includes("drink")) return "Food + drink";
  if (categories.includes("food")) return "Food";
  if (categories.includes("drink")) return "Drink";
  return "General";
}

function restaurantLabel(name, city) {
  const suffix = ` - ${city}`;
  return city && name.endsWith(suffix) ? name.slice(0, -suffix.length) : name;
}

function locationKeyForDeal(deal) {
  const location = deal.location || {};
  return [deal.restaurant, location.address || deal.city].join("|");
}

function locationSlug(group) {
  return group.key
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function dealText(deal) {
  return [
    deal.restaurant,
    deal.city,
    deal.summary,
    deal.candidate_text,
    deal.validity,
    deal.location?.address,
    ...(deal.details || []),
    ...(deal.tags || []),
    ...(deal.categories || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function matchesDay(deal) {
  const day = selectedDay();
  if (!day) return true;
  if (state.day === "today" && !matchesRecurringDate(deal, new Date())) return false;
  const days = deal.applies_days || [];
  return !days.length || days.includes(day) || DAYS.every((item) => days.includes(item));
}

function recurringMonthDays(deal) {
  const explicit = (deal.applies_month_days || [])
    .map(Number)
    .filter((value) => Number.isInteger(value) && value >= 1 && value <= 31);
  if (explicit.length) return new Set(explicit);

  const text = dealText(deal);
  const found = new Set();
  const patterns = [
    /\b(?:every|each)\s+(\d{1,2})(?:st|nd|rd|th)(?:\s+of\s+(?:the\s+)?month)?\b/gi,
    /\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\s+of\s+(?:each|every|the)\s+month\b/gi,
  ];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const value = Number(match[1]);
      if (value >= 1 && value <= 31) found.add(value);
    }
  }
  return found;
}

function matchesRecurringDate(deal, date) {
  const monthDays = recurringMonthDays(deal);
  return !monthDays.size || monthDays.has(date.getDate());
}

function daysInSegment(value = "") {
  const labels = DAYS.map((day) => DAY_LABELS[day].toLowerCase());
  const result = new Set();
  const text = value.toLowerCase();
  const range = text.match(/\b(mon|tue|wed|thu|fri|sat|sun)\s*[-–—]\s*(mon|tue|wed|thu|fri|sat|sun)\b/);
  if (range) {
    const start = labels.indexOf(range[1]);
    const end = labels.indexOf(range[2]);
    if (start >= 0 && end >= 0) {
      for (let index = start; index <= end; index += 1) result.add(DAYS[index]);
    }
  }
  for (const day of DAYS) {
    if (new RegExp(`\\b${DAY_LABELS[day]}(?:day)?\\b`, "i").test(text)) result.add(day);
  }
  return result;
}

function clockMinutes(hourText, minuteText, period, inferredPeriod = "") {
  let hour = Number(hourText);
  const minute = Number(minuteText || 0);
  const marker = (period || inferredPeriod || "").toLowerCase();
  if (marker === "pm" && hour < 12) hour += 12;
  if (marker === "am" && hour === 12) hour = 0;
  return hour * 60 + minute;
}

function timeRanges(value = "", location = {}) {
  const ranges = [];
  const pattern = /(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|–|—)\s*(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|(close))/gi;
  for (const match of value.matchAll(pattern)) {
    let startPeriod = match[3] || "";
    const endPeriod = match[6] || "";
    if (!startPeriod && /^(?:am|pm)$/i.test(endPeriod)) {
      startPeriod = Number(match[1]) > Number(match[4]) && endPeriod.toLowerCase() === "pm" ? "am" : endPeriod;
    }
    const start = clockMinutes(match[1], match[2], startPeriod);
    let end;
    if (match[7]) {
      const todayHours = location.hours?.[todayKey()] || [];
      end = todayHours.length ? parseClock(todayHours[todayHours.length - 1].close) : 24 * 60;
    } else {
      end = clockMinutes(match[4], match[5], endPeriod, startPeriod);
    }
    ranges.push({ start, end: end <= start ? end + 24 * 60 : end });
  }
  return ranges;
}

function dealAvailableNow(deal) {
  const currentDay = todayKey();
  if (!matchesRecurringDate(deal, new Date())) return false;
  const applies = deal.applies_days || [];
  if (applies.length && !applies.includes(currentDay) && !DAYS.every((day) => applies.includes(day))) return false;
  const business = openStatus(deal.location || {});
  if (business.startsWith("Closed")) return false;

  const windowText = deal.time_window || "";
  if (!windowText || /\ball day\b/i.test(windowText)) return true;
  const now = new Date();
  const minute = now.getHours() * 60 + now.getMinutes();
  const segments = windowText.split(/\s*;\s*/);
  let foundApplicableSchedule = false;
  let parsedApplicableRange = false;
  for (const segment of segments) {
    const segmentDays = daysInSegment(segment);
    if (segmentDays.size) {
      foundApplicableSchedule = true;
      if (!segmentDays.has(currentDay)) continue;
    }
    const ranges = timeRanges(segment, deal.location || {});
    if (!ranges.length) continue;
    parsedApplicableRange = true;
    if (ranges.some((range) => minute >= range.start && minute < range.end)) return true;
  }
  if (parsedApplicableRange || foundApplicableSchedule) return false;
  return true;
}

function matchesFilters(deal) {
  const categories = deal.categories || ["general"];
  const isHappyHour = (deal.tags || []).includes("happy_hour") || /happy\s*hour/i.test(dealText(deal));
  const knownTypes = categories.filter((category) => DEFAULT_TYPES.includes(category));
  const typeMatch = knownTypes.length
    ? knownTypes.some((category) => state.types.has(category))
    : state.types.size === DEFAULT_TYPES.length;
  const kindMatch = state.kinds.has(isHappyHour ? "happy_hour" : "other");
  const isChain = CHAIN_PATTERN.test(deal.restaurant || "");
  const distance = state.userLocation && deal.location
    ? distanceMiles(state.userLocation, deal.location)
    : Number.POSITIVE_INFINITY;
  return (
    (!state.query || dealText(deal).includes(state.query.toLowerCase())) &&
    (!state.restaurant || deal.restaurant === state.restaurant) &&
    (!state.cities.size || state.cities.has(deal.city)) &&
    deal.status === "active" &&
    typeMatch &&
    kindMatch &&
    (state.includeChains || !isChain) &&
    (!state.radius || distance <= Number(state.radius)) &&
    (!state.availableNow || dealAvailableNow(deal)) &&
    (!state.favoritesOnly || state.favorites.has(locationKeyForDeal(deal))) &&
    (!state.newOnly || isNewDeal(deal)) &&
    matchesDay(deal)
  );
}

function sourceMap() {
  const map = new Map();
  for (const source of state.payload.sources || []) {
    map.set(source.url, source);
  }
  return map;
}

function groupDeals(deals) {
  const sources = sourceMap();
  const groups = new Map();
  for (const deal of deals) {
    const source = sources.get(deal.source_url) || {};
    const location = deal.location || source.location || {};
    const key = locationKeyForDeal({ ...deal, location });
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        restaurant: deal.restaurant,
        city: deal.city,
        location,
        urls: new Set(),
        deals: [],
      });
    }
    const group = groups.get(key);
    group.urls.add(deal.source_url);
    group.deals.push(deal);
  }

  return [...groups.values()].sort((a, b) => {
    const distanceA = distanceToGroup(a);
    const distanceB = distanceToGroup(b);
    if (state.sort === "distance" && state.userLocation && Number.isFinite(distanceA) && Number.isFinite(distanceB)) {
      return distanceA - distanceB || a.restaurant.localeCompare(b.restaurant);
    }
    if (state.sort === "newest") {
      const newestA = Math.max(...a.deals.map(firstSeenTime));
      const newestB = Math.max(...b.deals.map(firstSeenTime));
      return newestB - newestA || a.restaurant.localeCompare(b.restaurant);
    }
    return a.restaurant.localeCompare(b.restaurant) || a.city.localeCompare(b.city);
  });
}

function renderSelectOptions(select, values, firstLabel) {
  select.innerHTML = `<option value="">${firstLabel}</option>`;
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function updateCitySummary() {
  citySummaryEl.textContent = state.cities.size === 0
    ? "All cities"
    : state.cities.size === 1
      ? [...state.cities][0]
      : `${state.cities.size} cities`;
}

function updateOfferSummary() {
  const disabled = [];
  if (!state.types.has("food")) disabled.push("food");
  if (!state.types.has("drink")) disabled.push("drinks");
  if (!state.kinds.has("happy_hour")) disabled.push("happy hour");
  if (!state.kinds.has("other")) disabled.push("other deals");
  if (!state.includeChains) disabled.push("chains");
  offerSummaryEl.textContent = disabled.length ? `Excluding ${disabled.join(", ")}` : "All deals";
}

function renderCityOptions(values) {
  cityOptionsEl.innerHTML = "";
  for (const city of values) {
    const label = document.createElement("label");
    label.className = "city-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = city;
    input.checked = state.cities.has(city);
    label.append(input, document.createTextNode(city));
    cityOptionsEl.append(label);
  }
  const clear = document.createElement("button");
  clear.className = "city-clear";
  clear.type = "button";
  clear.textContent = "All cities";
  cityOptionsEl.append(clear);
  updateCitySummary();
}

function renderFilterOptions() {
  const cities = new Set(state.payload.scope?.cities || []);
  const restaurants = new Set();
  for (const source of state.payload.sources || []) {
    if (source.city) cities.add(source.city);
    if (source.name) restaurants.add(source.name);
  }
  for (const deal of state.payload.deals || []) {
    if (deal.city) cities.add(deal.city);
    if (deal.restaurant) restaurants.add(deal.restaurant);
  }
  renderCityOptions([...cities].sort());
  renderSelectOptions(restaurantEl, [...restaurants].sort(), "All restaurants");
  updateOfferSummary();
}

function renderSummary(visibleDeals) {
  const { summary, generated_at: generatedAt, scope } = state.payload;
  const visibleGroups = groupDeals(visibleDeals);
  statsEl.innerHTML = `
    <span><strong>${visibleGroups.length}</strong> restaurants</span>
    <span><strong>${visibleDeals.length}</strong> deals</span>
  `;

  const locationText = state.locationMessage ? ` ${state.locationMessage}` : "";
  const dayText = state.day === "today" ? DAY_LABELS[todayKey()] : state.day ? tagLabel(state.day) : "any day";
  const ageHours = generatedAt ? (Date.now() - new Date(generatedAt).getTime()) / 3600000 : Number.POSITIVE_INFINITY;
  const delayed = ageHours > 36;
  metaEl.innerHTML = `
    <p><strong>${dayText}</strong> deals · Updated ${formatDate(generatedAt)}.${locationText}</p>
    ${delayed ? '<p class="feed-warning"><strong>Refresh delayed.</strong> Deals may be out of date while the automated update recovers.</p>' : ""}
  `;
}

function badge(text, className = "") {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

const ICONS = {
  directions: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s7-6.1 7-13a7 7 0 1 0-14 0c0 6.9 7 13 7 13Z"></path><circle cx="12" cy="9" r="2.3"></circle></svg>',
  phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.4 19.4 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.8a2 2 0 0 1-.4 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z"></path></svg>',
  source: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7"></path><path d="M10 14 21 3"></path><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"></path></svg>',
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3Z"></path></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"></circle><circle cx="6" cy="12" r="2.5"></circle><circle cx="18" cy="19" r="2.5"></circle><path d="m8.2 10.8 7.6-4.5M8.2 13.2l7.6 4.5"></path></svg>',
  report: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21V4"></path><path d="M5 5h11l-1 4 3 3H5"></path></svg>',
  chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>',
};

function actionLink(href, label, iconName, external = false) {
  const link = document.createElement("a");
  link.className = "icon-action";
  link.href = href;
  link.title = label;
  link.setAttribute("aria-label", label);
  link.innerHTML = ICONS[iconName];
  if (external) {
    link.target = "_blank";
    link.rel = "noopener";
  }
  link.addEventListener("click", (event) => {
    event.stopPropagation();
    trackEvent(`action_${iconName}`);
  });
  return link;
}

function actionButton(label, iconName, handler, active = false) {
  const button = document.createElement("button");
  button.className = `icon-action${active ? " is-active" : ""}`;
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(active));
  button.innerHTML = ICONS[iconName];
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    handler();
  });
  return button;
}

function reportUrl(group) {
  const restaurant = restaurantLabel(group.restaurant, group.city);
  const source = [...group.urls][0] || "";
  const params = new URLSearchParams({
    title: `[Deal report] ${restaurant} - ${group.city}`,
    body: `### Restaurant\n${restaurant}\n\n### City\n${group.city}\n\n### What needs attention?\nMissing or incorrect deal\n\n### Official source URL\n${source}\n\n### Details\n<!-- Describe what is missing or incorrect, including the right day, time, price, or expiration. -->`,
  });
  return `https://github.com/nickgggg/restaurant-deals/issues/new?${params}`;
}

function mapsUrl(name, address, googleMapsUrl) {
  if (googleMapsUrl) return googleMapsUrl;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${name} ${address}`)}`;
}

function telUrl(phone) {
  return `tel:${phone.replace(/[^0-9+]/g, "")}`;
}

function distanceMiles(a, b) {
  const radius = 3958.8;
  const toRad = (value) => (value * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * radius * Math.asin(Math.sqrt(h));
}

function distanceToGroup(group) {
  if (!state.userLocation || !group.location?.latitude || !group.location?.longitude) return Number.POSITIVE_INFINITY;
  return distanceMiles(state.userLocation, group.location);
}

function distanceLabel(group) {
  const distance = distanceToGroup(group);
  if (!Number.isFinite(distance)) return null;
  return `${distance.toFixed(distance < 10 ? 1 : 0)} mi`;
}

function minutesSinceWeekStart(date) {
  const jsDay = date.getDay();
  const dayIndex = jsDay === 0 ? 6 : jsDay - 1;
  return dayIndex * 1440 + date.getHours() * 60 + date.getMinutes();
}

function parseClock(value) {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

function openStatus(location) {
  const hours = location?.hours;
  if (!hours) return "Hours unknown";
  const now = new Date();
  const current = minutesSinceWeekStart(now);
  const today = DAYS[now.getDay() === 0 ? 6 : now.getDay() - 1];
  const ranges = [];

  DAYS.forEach((day, index) => {
    for (const range of hours[day] || []) {
      const start = index * 1440 + parseClock(range.open);
      let end = index * 1440 + parseClock(range.close);
      if (end <= start) end += 1440;
      ranges.push({ start, end, range, day });
    }
  });

  for (const item of ranges) {
    if (current >= item.start && current < item.end) return `Open until ${formatClock(item.range.close)}`;
    if (current + 1440 >= item.start && current + 1440 < item.end) return `Open until ${formatClock(item.range.close)}`;
  }

  const todayRanges = hours[today] || [];
  if (todayRanges.length) return `Closed now, opens ${formatClock(todayRanges[0].open)}`;
  return "Closed today";
}

function formatClock(value) {
  const [hour, minute] = value.split(":").map(Number);
  const period = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return `${displayHour}${minute ? `:${String(minute).padStart(2, "0")}` : ""}${period.toLowerCase()}`;
}

function bestDeals(group) {
  return group.deals
    .slice()
    .sort((a, b) => scoreDeal(b) - scoreDeal(a) || (a.summary || "").localeCompare(b.summary || ""))
    .slice(0, 2);
}

function scoreDeal(deal) {
  const tags = deal.tags || [];
  let score = 0;
  if (tags.includes("happy_hour")) score += 4;
  if (tags.includes("percent_off") || tags.includes("bogo") || tags.includes("free")) score += 3;
  if ((deal.applies_days || []).length) score += 2;
  if (deal.time_window) score += 1;
  return score;
}

function shareUrl(spot = "") {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.restaurant) params.set("restaurant", state.restaurant);
  if (state.cities.size) params.set("cities", [...state.cities].sort().join(","));
  if (state.day !== "today") params.set("day", state.day || "any");
  if (state.types.size !== DEFAULT_TYPES.length) params.set("types", [...state.types].sort().join(","));
  if (state.kinds.size !== DEFAULT_KINDS.length) params.set("offers", [...state.kinds].sort().join(","));
  if (!state.includeChains) params.set("chains", "0");
  if (state.availableNow) params.set("now", "1");
  if (state.newOnly) params.set("new", "1");
  if (state.sort === "newest") params.set("sort", "newest");
  if (state.view === "map") params.set("view", "map");
  if (spot) params.set("spot", spot);
  const query = params.toString();
  return `${location.origin}${location.pathname}${query ? `?${query}` : ""}`;
}

function syncUrl() {
  history.replaceState(null, "", shareUrl(state.spot));
}

async function shareResults(url = shareUrl(), title = "Restaurant Deals") {
  try {
    if (navigator.share) {
      await navigator.share({ title, url });
      return;
    }
    await navigator.clipboard.writeText(url);
    showToast("Link copied");
  } catch (error) {
    if (error.name !== "AbortError") showToast("Could not share this link");
  }
}

function readUrlState() {
  const params = new URLSearchParams(location.search);
  state.query = params.get("q") || "";
  state.restaurant = params.get("restaurant") || "";
  state.cities = new Set((params.get("cities") || "").split(",").filter(Boolean));
  const day = params.get("day");
  state.day = day === "any" ? "" : day && DAYS.includes(day) ? day : "today";
  const oldType = params.get("type");
  const oldOffer = params.get("offer");
  const types = params.has("types") ? params.get("types").split(",").filter((item) => DEFAULT_TYPES.includes(item)) : DEFAULT_TYPES;
  const kinds = params.has("offers") ? params.get("offers").split(",").filter((item) => DEFAULT_KINDS.includes(item)) : DEFAULT_KINDS;
  state.types = new Set(oldType && DEFAULT_TYPES.includes(oldType) ? [oldType] : types);
  state.kinds = new Set(oldOffer && DEFAULT_KINDS.includes(oldOffer) ? [oldOffer] : kinds);
  state.includeChains = params.get("chains") !== "0";
  state.sort = params.get("sort") === "newest" ? "newest" : "alpha";
  state.radius = "";
  state.availableNow = params.get("now") === "1";
  state.newOnly = params.get("new") === "1";
  state.view = params.get("view") === "map" ? "map" : "list";
  state.spot = params.get("spot") || "";
}

function syncControls() {
  searchEl.value = state.query;
  restaurantEl.value = state.restaurant;
  dayEl.value = state.day;
  for (const input of offerOptionsEl.querySelectorAll('input[type="checkbox"]')) {
    input.checked = input.value === "chains"
      ? state.includeChains
      : state.types.has(input.value) || state.kinds.has(input.value);
  }
  radiusEl.value = state.radius;
  radiusEl.disabled = !state.userLocation;
  sortLabelEl.textContent = state.sort === "distance" ? "Nearest" : state.sort === "newest" ? "Newest" : "A-Z";
  updateOfferSummary();
  availableNowEl.setAttribute("aria-pressed", String(state.availableNow));
  favoritesOnlyEl.setAttribute("aria-pressed", String(state.favoritesOnly));
  newOnlyEl.setAttribute("aria-pressed", String(state.newOnly));
  listViewEl.setAttribute("aria-pressed", String(state.view === "list"));
  mapViewEl.setAttribute("aria-pressed", String(state.view === "map"));
}

function renderDealRow(deal) {
  const row = document.createElement("article");
  row.className = "deal-row";

  const main = document.createElement("div");
  main.className = "deal-main";

  const titleLine = document.createElement("div");
  titleLine.className = "deal-title-line";
  const title = document.createElement("h3");
  title.textContent = normalizeScheduleText(deal.summary || deal.candidate_text);
  titleLine.append(title);
  if (isNewDeal(deal)) {
    const fresh = badge("New", "new-badge");
    fresh.title = `First found ${formatDate(deal.first_seen)}`;
    titleLine.append(fresh);
  }
  main.append(titleLine);

  const detailItems = (deal.details || []).filter((item) => item && item !== title.textContent).slice(0, 3);
  if (detailItems.length) {
    const details = document.createElement("p");
    details.className = "deal-details";
    details.textContent = normalizeScheduleText(detailItems.join(" · "));
    main.append(details);
  }

  const meta = document.createElement("div");
  meta.className = "deal-meta";
  const visibleText = [title.textContent, ...detailItems].join(" ");
  if (!isValidityRedundant(deal, visibleText)) {
    meta.append(badge(normalizeScheduleText(deal.validity), "validity"));
  }
  meta.append(badge(categoryLabel(deal.categories), "category"));
  const visibleTags = new Set(["happy_hour", "bogo", "percent_off", "free"]);
  const displayTags = (deal.tags || []).filter((tag) => visibleTags.has(tag)).slice(0, 1);
  for (const tag of displayTags) meta.append(badge(tagLabel(tag)));
  main.append(meta);

  if (deal.status === "stale") {
    const stale = document.createElement("p");
    stale.className = "stale-note";
    stale.textContent = `Not found in the latest check · Last seen ${formatDate(deal.last_seen)}`;
    main.append(stale);
  }

  row.append(main);
  return row;
}

function locationQualifier(address = "") {
  const street = address.split(",")[0].replace(/^\d+\s+/, "").replace(/\s+(?:Ste|Suite|Unit|#)\s*\S+.*$/i, "").trim();
  if (/^(?:CA-1|Pacific Coast (?:Hwy|Highway))$/i.test(street)) return "PCH";
  return street;
}

function renderGroup(group, needsQualifier = false) {
  const section = document.createElement("details");
  section.className = "location";
  section.id = locationSlug(group);
  section.open = Boolean(state.restaurant || state.query);

  const summary = document.createElement("summary");
  summary.className = "location-heading";

  const titleWrap = document.createElement("div");
  titleWrap.className = "location-title";
  const title = document.createElement("h2");
  const baseLabel = restaurantLabel(group.restaurant, group.city);
  const qualifier = needsQualifier ? locationQualifier(group.location?.address) : "";
  title.textContent = qualifier ? `${baseLabel} · ${qualifier}` : baseLabel;
  titleWrap.append(title);
  const sub = document.createElement("p");
  const bits = [group.city, distanceLabel(group), openStatus(group.location)].filter(Boolean);
  sub.textContent = bits.join(" · ");
  titleWrap.append(sub);

  const preview = document.createElement("div");
  preview.className = "deal-preview";
  for (const deal of bestDeals(group)) preview.append(badge(normalizeScheduleText(deal.summary || "Deal")));

  const actions = document.createElement("div");
  actions.className = "location-actions";
  actions.append(badge(`${group.deals.length} ${group.deals.length === 1 ? "deal" : "deals"}`, "deal-count"));
  const favorite = state.favorites.has(group.key);
  actions.append(actionButton(favorite ? "Remove favorite" : "Save favorite", "star", () => {
    if (favorite) state.favorites.delete(group.key);
    else state.favorites.add(group.key);
    const persisted = saveFavorites();
    trackEvent(favorite ? "favorite_remove" : "favorite_add");
    showToast(favorite
      ? "Favorite removed"
      : persisted
        ? "Saved on this device; private sessions may clear it"
        : "Saved for this session only");
    rerender();
  }, favorite));
  if (group.location?.address) {
    actions.append(actionLink(mapsUrl(group.restaurant, group.location.address, group.location.google_maps_url), "Directions", "directions", true));
  }
  if (group.location?.phone) {
    actions.append(actionLink(telUrl(group.location.phone), `Call ${group.location.phone}`, "phone"));
  }
  for (const [index, url] of [...group.urls].entries()) {
    const label = group.urls.size > 1 ? `Official source ${index + 1}` : "Official source";
    actions.append(actionLink(url, label, "source", true));
  }
  actions.append(actionButton("Share restaurant", "share", () => {
    trackEvent("share_restaurant");
    shareResults(shareUrl(locationSlug(group)), `${baseLabel} deals`);
  }));
  actions.append(actionLink(reportUrl(group), "Report missing or incorrect deal", "report", true));

  const disclosure = document.createElement("span");
  disclosure.className = "disclosure";
  disclosure.innerHTML = ICONS.chevron;

  summary.append(titleWrap, preview, actions, disclosure);
  section.append(summary);

  const body = document.createElement("div");
  body.className = "location-body";
  const rows = document.createElement("div");
  rows.className = "deal-list";
  const displayDeals = state.sort === "newest"
    ? group.deals.slice().sort((a, b) => firstSeenTime(b) - firstSeenTime(a))
    : group.deals;
  for (const deal of displayDeals) rows.append(renderDealRow(deal));
  body.append(rows);
  section.append(body);
  return section;
}

function loadMapLibrary() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (mapLibraryPromise) return mapLibraryPromise;
  mapLibraryPromise = new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${MAPLIBRE_CSS_URL}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = MAPLIBRE_CSS_URL;
      document.head.append(link);
    }
    const script = document.createElement("script");
    script.src = MAPLIBRE_URL;
    script.onload = () => resolve(window.maplibregl);
    script.onerror = () => reject(new Error("Map library could not load"));
    document.head.append(script);
  });
  return mapLibraryPromise;
}

function mapStyle() {
  return "https://tiles.openfreemap.org/styles/positron";
}

function openSpot(group) {
  state.view = "list";
  state.spot = locationSlug(group);
  syncControls();
  syncUrl();
  renderDeals();
  requestAnimationFrame(() => {
    const section = document.getElementById(state.spot);
    if (!section) return;
    section.open = true;
    section.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function mapPopup(group) {
  const popup = document.createElement("article");
  popup.className = "deal-map-popup";
  const title = document.createElement("strong");
  title.textContent = restaurantLabel(group.restaurant, group.city);
  const meta = document.createElement("span");
  meta.textContent = [group.city, openStatus(group.location), `${group.deals.length} ${group.deals.length === 1 ? "deal" : "deals"}`].join(" · ");
  const preview = document.createElement("p");
  preview.textContent = normalizeScheduleText(bestDeals(group)[0]?.summary || "View current deals");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "View deals";
  button.addEventListener("click", () => openSpot(group));
  popup.append(title, meta, preview, button);
  return popup;
}

async function renderMap(groups) {
  mapMessageEl.hidden = true;
  const mappable = groups.filter((group) => Number.isFinite(group.location?.latitude) && Number.isFinite(group.location?.longitude));
  if (!mappable.length) {
    mapMessageEl.textContent = "No mapped restaurants match these filters.";
    mapMessageEl.hidden = false;
    return;
  }
  try {
    const maplibregl = await loadMapLibrary();
    if (!state.map) {
      state.map = new maplibregl.Map({
        container: "map",
        style: mapStyle(),
        center: [-117.98, 33.69],
        zoom: 10.5,
        attributionControl: false,
      });
      state.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      state.map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    }
    for (const marker of state.mapMarkers) marker.remove();
    state.mapMarkers = [];
    const bounds = new maplibregl.LngLatBounds();
    for (const group of mappable) {
      const element = document.createElement("button");
      element.type = "button";
      element.className = `deal-marker${state.favorites.has(group.key) ? " is-favorite" : ""}`;
      element.textContent = group.deals.length;
      element.title = `${restaurantLabel(group.restaurant, group.city)} · ${group.deals.length} ${group.deals.length === 1 ? "deal" : "deals"}`;
      const popup = new maplibregl.Popup({ offset: 18, closeButton: false }).setDOMContent(mapPopup(group));
      const marker = new maplibregl.Marker({ element })
        .setLngLat([group.location.longitude, group.location.latitude])
        .setPopup(popup)
        .addTo(state.map);
      state.mapMarkers.push(marker);
      bounds.extend([group.location.longitude, group.location.latitude]);
    }
    state.map.resize();
    state.map.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: 350 });
  } catch (error) {
    mapMessageEl.textContent = "The custom map could not load. The deal list is still available.";
    mapMessageEl.hidden = false;
  }
}

function renderDeals() {
  const deals = (state.payload.deals || []).filter(matchesFilters);
  const groups = groupDeals(deals);
  const nameCounts = groups.reduce((counts, group) => counts.set(group.restaurant, (counts.get(group.restaurant) || 0) + 1), new Map());
  dealsEl.innerHTML = "";
  renderSummary(deals);
  syncControls();
  syncUrl();
  const mapActive = state.view === "map";
  dealsEl.hidden = mapActive;
  mapPanelEl.hidden = !mapActive;
  if (mapActive) renderMap(groups);

  if (!groups.length) {
    const selected = (state.restaurants?.restaurants || []).find((item) => item.name === state.restaurant);
    if (selected) {
      const link = selected.website_url
        ? ` <a href="${selected.website_url}" target="_blank" rel="noopener">Check its official site</a>.`
        : "";
      dealsEl.innerHTML = `<p class="empty"><strong>No verified special found for ${selected.name} yet.</strong>${link}</p>`;
    } else {
      dealsEl.innerHTML = '<p class="empty">No matching verified deals found.</p>';
    }
    return;
  }

  for (const group of groups) dealsEl.append(renderGroup(group, nameCounts.get(group.restaurant) > 1));
  if (state.spot) {
    requestAnimationFrame(() => {
      const section = document.getElementById(state.spot);
      if (section) section.open = true;
    });
  }
}

function renderSources() {
  const failed = (state.payload.sources || []).filter((source) => !source.ok && !source.retired);
  if (!failed.length) {
    sourcesEl.innerHTML = "";
    return;
  }
  sourcesEl.innerHTML = `
    <h2>Sources Needing Attention</h2>
    ${failed
      .map(
        (source) => `
          <article>
            <strong>${source.name}</strong>
            <a href="${source.url}" target="_blank" rel="noopener">${source.url}</a>
            <p>${source.error}</p>
          </article>
        `
      )
      .join("")}
  `;
}

function compactNumber(value) {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
}

function dateOnly(value) {
  if (!value) return "Pending";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}

function estimatedActivation(statuses, city, queuedCities) {
  const interval = state.project?.queue_activation_days || 7;
  const activeDates = Object.values(statuses)
    .filter((item) => item.status === "active" && item.activated_at)
    .map((item) => new Date(item.activated_at).getTime());
  const latest = Math.max(...activeDates);
  const queueIndex = queuedCities.indexOf(city);
  if (!Number.isFinite(latest) || queueIndex < 0) return null;
  return new Date(latest + (queueIndex + 1) * interval * 86400000);
}

function renderProjectStatus() {
  const coverage = state.restaurants?.coverage || {};
  const pipeline = state.payload?.pipeline || {};
  const statuses = state.restaurants?.area_status || {};
  const activeCities = coverage.cities || [];
  const queuedCities = coverage.queued_cities || [];
  coverageSummaryEl.textContent = `${activeCities.length} live · ${queuedCities.length} queued · next ${dateOnly(state.restaurants?.refresh_after)}`;

  coverageCitiesEl.innerHTML = "";
  for (const city of [...activeCities, ...queuedCities]) {
    const info = statuses[city] || {};
    const active = info.status === "active";
    const row = document.createElement("div");
    row.className = "coverage-row";
    const name = document.createElement("strong");
    name.textContent = city;
    const status = badge(active ? "Live" : "Queued", `coverage-state ${active ? "live" : "queued"}`);
    const note = document.createElement("span");
    note.className = "coverage-date";
    const estimate = estimatedActivation(statuses, city, queuedCities);
    const cityCount = info.restaurant_count ?? (state.restaurants?.restaurants || []).filter((item) => item.city === city).length;
    note.textContent = active
      ? `${compactNumber(cityCount)} spots · scanned ${dateOnly(info.last_refreshed)}`
      : `Est. ${dateOnly(estimate)}`;
    row.append(name, status, note);
    coverageCitiesEl.append(row);
  }

  const metrics = [
    ["Restaurants found", compactNumber(coverage.restaurant_count)],
    ["Official websites", compactNumber(coverage.official_websites)],
    ["Specials pages", compactNumber(coverage.specials_pages_found)],
    ["Candidate pages", compactNumber(pipeline.candidate_pages)],
    ["Reported sources", compactNumber(pipeline.reported_pages)],
    ["Browser renders", `${compactNumber(pipeline.rendered_pages)}/${compactNumber(pipeline.render_attempts)}`],
    ["Visual deal pages", compactNumber(pipeline.visual_pages)],
    ["Visual checks", `${compactNumber(pipeline.visual_attempts)} calls · ${compactNumber(pipeline.visual_cache_hits)} cached`],
    ["Sites checked", `${state.project?.max_source_sites_per_run || 12}/run`],
    ["Gemini batch", `${state.project?.gemini_pages_per_run || 30}/run`],
    ["Gemini visual", `${state.project?.gemini_visual_pages_per_run || 4}/run`],
    ["City activation", `1/${state.project?.queue_activation_days || 7} days`],
    ["City rescan", `${state.project?.area_refresh_days || 35} days`],
  ];
  coverageMetricsEl.innerHTML = metrics
    .map(([label, value]) => `<div><strong>${value}</strong><span>${label}</span></div>`)
    .join("");
  costNoteEl.textContent = state.project?.cost_note || "The pipeline is configured around free-tier limits.";

  roadmapListEl.innerHTML = (state.project?.roadmap || [])
    .map((item) => `
      <article>
        <span class="roadmap-state ${item.status}">${item.status}</span>
        <div><strong>${item.title}</strong><p>${item.detail}</p></div>
      </article>
    `)
    .join("");
}

function rerender() {
  renderDeals();
  renderSources();
}

function requestLocation() {
  if (!navigator.geolocation) {
    state.locationMessage = "Location is not available in this browser.";
    state.sort = "alpha";
    rerender();
    return;
  }
  sortLabelEl.textContent = "Locating...";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      state.userLocation = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      state.sort = "distance";
      state.locationMessage = "Nearest first.";
      radiusEl.disabled = false;
      sortLabelEl.textContent = "Nearest";
      trackEvent("sort_distance");
      renderFilterSummary();
      rerender();
    },
    () => {
      state.locationMessage = "Location permission was not enabled.";
      state.sort = "alpha";
      sortLabelEl.textContent = "A-Z";
      rerender();
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
  );
}

async function init() {
  const [dealsResponse, restaurantsResponse, projectResponse] = await Promise.all([
    fetch("data/deals.json", { cache: "no-store" }),
    fetch("data/restaurants.json", { cache: "no-store" }).catch(() => null),
    fetch("data/project.json", { cache: "no-store" }).catch(() => null),
  ]);
  state.payload = await dealsResponse.json();
  state.restaurants = restaurantsResponse?.ok ? await restaurantsResponse.json() : null;
  state.project = projectResponse?.ok ? await projectResponse.json() : null;
  renderConnectionStatus();
  readUrlState();
  renderFilterOptions();
  syncControls();
  renderFilterSummary();
  renderProjectStatus();
  renderDeals();
}

searchEl.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  state.spot = "";
  renderFilterSummary();
  rerender();
});

restaurantEl.addEventListener("change", (event) => {
  state.restaurant = event.target.value;
  state.spot = "";
  renderFilterSummary();
  rerender();
  if (state.restaurant) {
    requestAnimationFrame(() => {
      const group = groupDeals((state.payload.deals || []).filter(matchesFilters))[0];
      const section = group && document.getElementById(locationSlug(group));
      if (section) {
        section.open = true;
        section.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }
});

cityOptionsEl.addEventListener("change", (event) => {
  if (!event.target.matches('input[type="checkbox"]')) return;
  if (event.target.checked) state.cities.add(event.target.value);
  else state.cities.delete(event.target.value);
  state.spot = "";
  updateCitySummary();
  renderFilterSummary();
  rerender();
});

offerOptionsEl.addEventListener("change", (event) => {
  if (!event.target.matches('input[type="checkbox"]')) return;
  const { value, checked } = event.target;
  if (value === "chains") state.includeChains = checked;
  else if (DEFAULT_TYPES.includes(value)) {
    if (checked) state.types.add(value);
    else state.types.delete(value);
  } else if (DEFAULT_KINDS.includes(value)) {
    if (checked) state.kinds.add(value);
    else state.kinds.delete(value);
  }
  state.spot = "";
  updateOfferSummary();
  renderFilterSummary();
  trackEvent("filter_offer", { value, enabled: checked });
  rerender();
});

cityOptionsEl.addEventListener("click", (event) => {
  if (!event.target.matches(".city-clear")) return;
  state.cities.clear();
  state.spot = "";
  for (const input of cityOptionsEl.querySelectorAll('input[type="checkbox"]')) input.checked = false;
  updateCitySummary();
  renderFilterSummary();
  rerender();
});

document.addEventListener("click", (event) => {
  if (!cityPickerEl.contains(event.target)) cityPickerEl.open = false;
  if (!offerPickerEl.contains(event.target)) offerPickerEl.open = false;
  if (!sortMenuEl.contains(event.target)) sortMenuEl.open = false;
});

dayEl.addEventListener("change", (event) => {
  state.day = event.target.value;
  state.spot = "";
  renderFilterSummary();
  rerender();
});

radiusEl.addEventListener("change", (event) => {
  state.radius = event.target.value;
  state.spot = "";
  renderFilterSummary();
  trackEvent("filter_radius", { miles: state.radius || "any" });
  rerender();
});

availableNowEl.addEventListener("click", () => {
  state.availableNow = !state.availableNow;
  state.spot = "";
  if (state.availableNow) {
    state.day = "today";
    dayEl.value = "today";
  }
  renderFilterSummary();
  trackEvent("filter_available_now", { enabled: state.availableNow });
  rerender();
});

favoritesOnlyEl.addEventListener("click", () => {
  state.favoritesOnly = !state.favoritesOnly;
  state.spot = "";
  renderFilterSummary();
  trackEvent("filter_favorites", { enabled: state.favoritesOnly });
  rerender();
});

newOnlyEl.addEventListener("click", () => {
  state.newOnly = !state.newOnly;
  state.spot = "";
  renderFilterSummary();
  trackEvent("filter_new", { enabled: state.newOnly });
  rerender();
});

listViewEl.addEventListener("click", () => {
  state.view = "list";
  filtersEl.classList.remove("is-open");
  filterToggleEl.setAttribute("aria-expanded", "false");
  rerender();
});

mapViewEl.addEventListener("click", () => {
  state.view = "map";
  state.spot = "";
  filtersEl.classList.remove("is-open");
  filterToggleEl.setAttribute("aria-expanded", "false");
  trackEvent("view_map");
  rerender();
});

shareViewEl.addEventListener("click", () => {
  trackEvent("share_results");
  shareResults();
});

filterToggleEl.addEventListener("click", () => {
  const expanded = filtersEl.classList.toggle("is-open");
  filterToggleEl.setAttribute("aria-expanded", String(expanded));
  trackEvent("filters_toggle", { expanded });
});

sortMenuEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sort]");
  if (!button) return;
  sortMenuEl.open = false;
  if (button.dataset.sort === "distance") {
    if (!state.userLocation) requestLocation();
    else {
      state.sort = "distance";
      state.locationMessage = "Nearest first.";
      rerender();
    }
  } else if (button.dataset.sort === "newest") {
    state.sort = "newest";
    state.locationMessage = "Newest discoveries first.";
    trackEvent("sort_newest");
    rerender();
  } else {
    state.sort = "alpha";
    state.locationMessage = "Sorted A-Z.";
    state.radius = "";
    radiusEl.value = "";
    trackEvent("sort_alpha");
    rerender();
  }
  syncControls();
  renderFilterSummary();
});

function syncThemeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  const label = dark ? "Use light mode" : "Use dark mode";
  themeToggleEl.setAttribute("aria-label", label);
  themeToggleEl.title = label;
}

themeToggleEl.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("theme", next);
  syncThemeButton();
});

window.addEventListener("online", () => {
  cachedDataUsed = false;
  renderConnectionStatus();
  showToast("Back online · refreshing deals");
  window.setTimeout(() => window.location.reload(), 500);
});

window.addEventListener("offline", renderConnectionStatus);

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  installAppEl.hidden = false;
});

installAppEl.addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  const result = await installPrompt.userChoice;
  trackEvent("install_app", { outcome: result.outcome });
  installPrompt = null;
  installAppEl.hidden = true;
});

window.addEventListener("appinstalled", () => {
  installPrompt = null;
  installAppEl.hidden = true;
  showToast("Restaurant Deals installed");
});

renderFilterSummary();
syncThemeButton();

registerServiceWorker();
init().catch((error) => {
  dealsEl.innerHTML = `<p class="empty">Could not load deals: ${error.message}</p>`;
});
