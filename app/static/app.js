"use strict";

/**
 * AirWise front end. Vanilla JS, no dependencies.
 * All server data is written with textContent (never innerHTML) to prevent XSS.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("advice-form");
  const submitBtn = $("submit-btn");
  const errorBox = $("form-error");
  const result = $("result");
  let coords = null;
  let payloadLanguage = "en";

  const POLLUTANT_LABELS = {
    pm2_5: "PM2.5 (fine particles)",
    pm10: "PM10 (coarse dust)",
    ozone: "Ozone (O₃)",
    nitrogen_dioxide: "Nitrogen dioxide (NO₂)",
    sulphur_dioxide: "Sulphur dioxide (SO₂)",
    carbon_monoxide: "Carbon monoxide (CO)",
  };

  const hhmm = (iso) => (iso || "").slice(11, 16);

  const FIELD_IDS = ["city", "duration"];

  class FieldError extends Error {
    constructor(message, fieldId) {
      super(message);
      this.fieldId = fieldId;
    }
  }

  /** Show a message and, if a field is named, mark it invalid for assistive tech. */
  function showError(message, fieldId) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    if (fieldId) {
      const field = $(fieldId);
      field.setAttribute("aria-invalid", "true");
      field.setAttribute("aria-describedby", "form-error");
      field.focus();
    }
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
    FIELD_IDS.forEach((id) => {
      $(id).removeAttribute("aria-invalid");
    });
    $("duration").removeAttribute("aria-describedby");
    $("city").setAttribute("aria-describedby", "city-hint");
  }

  function fillList(listEl, items) {
    listEl.replaceChildren(
      ...items.map((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        return li;
      }),
    );
  }

  function row(cells) {
    const tr = document.createElement("tr");
    cells.forEach((text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    return tr;
  }

  $("locate-btn").addEventListener("click", () => {
    const status = $("locate-status");
    if (!("geolocation" in navigator)) {
      status.textContent = "Location is not supported by this browser. Please type a city.";
      return;
    }
    status.textContent = "Finding your location…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        coords = {
          latitude: Number(pos.coords.latitude.toFixed(4)),
          longitude: Number(pos.coords.longitude.toFixed(4)),
        };
        status.textContent = "Location found. Press “Get my advice”.";
      },
      () => {
        coords = null;
        status.textContent = "Couldn't get your location. Please type a city instead.";
      },
      { timeout: 10000, maximumAge: 600000 },
    );
  });

  $("city").addEventListener("input", () => {
    coords = null; // typing a city overrides a previously detected location
  });

  function buildPayload() {
    const city = $("city").value.trim();
    const duration = Number($("duration").value);
    if (!city && !coords) {
      throw new FieldError("Please enter a city or use your location.", "city");
    }
    if (!Number.isInteger(duration) || duration < 5 || duration > 480) {
      throw new FieldError("Duration must be a whole number between 5 and 480 minutes.", "duration");
    }
    const payload = {
      profile: $("profile").value,
      activity: $("activity").value,
      duration_minutes: duration,
      language: $("language").value,
    };
    if (city) payload.city = city;
    else Object.assign(payload, coords);
    const question = $("question").value.trim();
    if (question) payload.question = question;
    return payload;
  }

  function render(data) {
    const { air, category, decision } = data;

    const badge = $("aqi-badge");
    badge.style.background = category.color;
    badge.style.color = category.level === 2 ? "#1b1f23" : "#ffffff";
    badge.setAttribute("aria-label", `US AQI ${air.aqi}, ${category.label}`);
    $("aqi-value").textContent = String(air.aqi);
    $("location").textContent = `${air.location_name} · ${hhmm(air.observed_at)} local time`;
    $("category").textContent = category.label;
    $("category-msg").textContent = category.health_message;

    $("verdict").dataset.verdict = decision.verdict;
    $("headline").textContent = decision.headline;
    $("risk").textContent = String(decision.risk_score);
    const meter = $("risk-meter");
    meter.value = decision.risk_score;
    meter.setAttribute("aria-valuetext", `${decision.risk_score} out of 100`);

    const explanation = $("explanation");
    explanation.textContent = data.explanation;
    // The page is English; mark Hindi text so screen readers pick the right voice (WCAG 3.1.2).
    explanation.lang = data.ai_generated && payloadLanguage === "hi" ? "hi" : "en";
    $("ai-note").textContent = data.ai_generated
      ? "Explanation written by Google Gemini from the data above."
      : "Explanation generated by AirWise's built-in rules.";

    fillList($("actions"), decision.actions);
    $("mask").textContent = decision.mask;
    $("window").textContent = decision.best_window
      ? `Cleaner air expected ${hhmm(decision.best_window.start)}–${hhmm(decision.best_window.end)} (AQI about ${decision.best_window.average_aqi}).`
      : "";
    fillList($("indoor"), decision.indoor_tips);

    $("pollutants").replaceChildren(
      ...Object.entries(air.pollutants)
        .filter(([, v]) => v !== null)
        .map(([k, v]) => row([POLLUTANT_LABELS[k] || k, v.toFixed(1)])),
    );
    $("forecast").replaceChildren(
      ...air.forecast.map((p) => row([hhmm(p.time), String(p.aqi), p.category || ""])),
    );

    result.hidden = false;
    $("result-title").focus();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    let payload;
    try {
      payload = buildPayload();
      payloadLanguage = payload.language;
    } catch (err) {
      showError(err.message, err.fieldId);
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Checking the air…";
    result.setAttribute("aria-busy", "true");
    try {
      const res = await fetch("/api/advice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = Array.isArray(data.detail)
          ? "Please check your inputs and try again."
          : data.detail || "Something went wrong. Please try again.";
        throw new Error(detail);
      }
      render(data);
    } catch (err) {
      showError(err.message || "Network error. Please check your connection.");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Get my advice";
      result.setAttribute("aria-busy", "false");
    }
  });
})();
