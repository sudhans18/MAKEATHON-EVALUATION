(function () {
  "use strict";

  var state = {
    user: null,
    teams: [],
    currentTeamId: null,
    evaluation: null,
    dirty: false,
    saveTimer: null,
    saveInFlight: false,
    pendingRetry: false,
    lastSavedAt: null,
  };

  var els = {
    loginView: document.getElementById("loginView"),
    judgeView: document.getElementById("judgeView"),
    loginForm: document.getElementById("loginForm"),
    nameInput: document.getElementById("nameInput"),
    passwordInput: document.getElementById("passwordInput"),
    networkBanner: document.getElementById("networkBanner"),
    alertBanner: document.getElementById("alertBanner"),
    sessionActions: document.getElementById("sessionActions"),
    judgeName: document.getElementById("judgeName"),
    refreshTeamsBtn: document.getElementById("refreshTeamsBtn"),
    logoutBtn: document.getElementById("logoutBtn"),
    teamsGrid: document.getElementById("teamsGrid"),
    teamCount: document.getElementById("teamCount"),
    evaluationPanel: document.getElementById("evaluationPanel"),
    evalTitle: document.getElementById("evalTitle"),
    evalStatusBadge: document.getElementById("evalStatusBadge"),
    deadlineText: document.getElementById("deadlineText"),
    problemText: document.getElementById("problemText"),
    solutionText: document.getElementById("solutionText"),
    criteriaGrid: document.getElementById("criteriaGrid"),
    remarksInput: document.getElementById("remarksInput"),
    saveState: document.getElementById("saveState"),
    saveDraftBtn: document.getElementById("saveDraftBtn"),
    submitBtn: document.getElementById("submitBtn"),
    retrySaveBtn: document.getElementById("retrySaveBtn"),
  };

  function setBanner(el, message, type) {
    if (!message) {
      el.className = "banner hidden";
      el.textContent = "";
      return;
    }
    el.className = "banner " + (type || "info");
    el.textContent = message;
  }

  function setNetworkStatus() {
    if (navigator.onLine) {
      if (state.pendingRetry) {
        setBanner(els.networkBanner, "Back online. Retrying draft save...", "info");
      } else {
        setBanner(els.networkBanner, "", "");
      }
    } else {
      setBanner(els.networkBanner, "You are offline. Changes will retry when connection returns.", "error");
    }
  }

  function showAlert(message, type) {
    setBanner(els.alertBanner, message, type || "error");
    if (message) {
      window.setTimeout(function () {
        if (els.alertBanner.textContent === message) {
          setBanner(els.alertBanner, "", "");
        }
      }, 4500);
    }
  }

  async function api(path, options) {
    var cfg = options || {};
    var headers = cfg.headers || {};
    if (!(cfg.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    try {
      var response = await fetch(path, {
        method: cfg.method || "GET",
        headers: headers,
        body: cfg.body,
        credentials: "include",
      });
      var ct = response.headers.get("content-type") || "";
      var data = ct.indexOf("application/json") >= 0 ? await response.json() : await response.text();
      if (!response.ok) {
        var err = (data && data.error) || ("Request failed with status " + response.status);
        throw new Error(err);
      }
      return data;
    } catch (error) {
      if (!navigator.onLine || /Failed to fetch|NetworkError/i.test(String(error.message || ""))) {
        setNetworkStatus();
      }
      throw error;
    }
  }

  function showView(viewName) {
    var isLogin = viewName === "login";
    els.loginView.classList.toggle("hidden", !isLogin);
    els.judgeView.classList.toggle("hidden", isLogin);
    els.sessionActions.hidden = isLogin;
  }

  function statusLabel(status) {
    if (status === "in_progress") return "In Progress";
    if (status === "submitted") return "Submitted";
    return "Not Started";
  }

  function getCurrentTeam() {
    for (var i = 0; i < state.teams.length; i += 1) {
      if (state.teams[i].id === state.currentTeamId) {
        return state.teams[i];
      }
    }
    return null;
  }

  function setSaveState(text) {
    els.saveState.textContent = text || "";
  }

  function renderTeams() {
    els.teamCount.textContent = state.teams.length + " teams";
    if (!state.teams.length) {
      els.teamsGrid.innerHTML = "<p class='muted'>No teams assigned yet.</p>";
      return;
    }
    els.teamsGrid.innerHTML = state.teams
      .map(function (team) {
        return (
          "<article class='team-card'>" +
          "<div class='section-head'>" +
          "<h3>" + escapeHtml(team.name) + "</h3>" +
          "<span class='status-badge status-" + team.status + "'>" + statusLabel(team.status) + "</span>" +
          "</div>" +
          "<p>Tap to open scoring form for this team.</p>" +
          "<button class='btn btn-secondary' data-team-id='" + team.id + "' type='button'>Open</button>" +
          "</article>"
        );
      })
      .join("");
  }

  function renderEvaluation() {
    var evaluation = state.evaluation;
    if (!evaluation) {
      els.evaluationPanel.classList.add("hidden");
      return;
    }

    var team = evaluation.team;
    els.evaluationPanel.classList.remove("hidden");
    els.evalTitle.textContent = team.name + " Evaluation";

    var currentTeam = getCurrentTeam();
    var status = currentTeam ? currentTeam.status : "not_started";
    els.evalStatusBadge.textContent = statusLabel(status);
    els.evalStatusBadge.className = "status-badge status-" + status;

    if (evaluation.submission_deadline) {
      els.deadlineText.textContent = "Deadline: " + evaluation.submission_deadline;
    } else {
      els.deadlineText.textContent = "Deadline: Not set by admin";
    }
    if (!evaluation.editable) {
      els.deadlineText.textContent += " (Editing locked)";
    }

    els.problemText.textContent = team.problem_statement || "No problem statement available.";
    els.solutionText.textContent = team.expected_solution || "No expected solution available.";

    var scoreMap = evaluation.scores || {};
    els.criteriaGrid.innerHTML = evaluation.criteria
      .map(function (c) {
        var existing = scoreMap[String(c.id)];
        var value = existing === null || typeof existing === "undefined" ? "" : String(existing);
        return (
          "<div class='criterion-row'>" +
          "<div>" +
          "<strong>" + escapeHtml(c.name) + "</strong>" +
          "<small>Range: 0 to " + c.max_score + "</small>" +
          "</div>" +
          "<input " +
          "type='number' " +
          "step='0.1' " +
          "min='0' " +
          "max='" + c.max_score + "' " +
          "data-criterion-id='" + c.id + "' " +
          "value='" + value + "' " +
          "/>" +
          "</div>"
        );
      })
      .join("");

    els.remarksInput.value = evaluation.remarks || "";
    state.dirty = false;
    state.pendingRetry = false;
    els.retrySaveBtn.classList.add("hidden");

    var isSubmitted = evaluation.submission && evaluation.submission.is_submitted === 1;
    var canEdit = evaluation.editable && !isSubmitted;
    setDisabledState(!canEdit);

    if (isSubmitted) {
      setSaveState("Final submission completed.");
    } else if (!evaluation.editable) {
      setSaveState("Editing locked due to submission deadline.");
    } else if (state.lastSavedAt) {
      setSaveState("Draft saved at " + state.lastSavedAt);
    } else {
      setSaveState("No unsaved changes.");
    }
  }

  function setDisabledState(disabled) {
    var inputs = els.criteriaGrid.querySelectorAll("input");
    for (var i = 0; i < inputs.length; i += 1) {
      inputs[i].disabled = disabled;
    }
    els.remarksInput.disabled = disabled;
    els.saveDraftBtn.disabled = disabled;
    els.submitBtn.disabled = disabled;
  }

  function markDirtyAndSchedule() {
    if (!state.evaluation || !state.evaluation.editable) return;
    if (state.evaluation.submission && state.evaluation.submission.is_submitted === 1) return;
    state.dirty = true;
    setSaveState("Unsaved changes...");
    if (state.saveTimer) {
      clearTimeout(state.saveTimer);
    }
    state.saveTimer = window.setTimeout(function () {
      saveDraft(true);
    }, 1500);
  }

  function collectDraftPayload() {
    var criteriaInputs = els.criteriaGrid.querySelectorAll("input[data-criterion-id]");
    var scores = {};
    for (var i = 0; i < criteriaInputs.length; i += 1) {
      var input = criteriaInputs[i];
      var criterionId = input.getAttribute("data-criterion-id");
      var raw = input.value.trim();
      scores[criterionId] = raw === "" ? null : Number(raw);
    }
    return {
      scores: scores,
      remarks: els.remarksInput.value || "",
    };
  }

  async function saveDraft(isAuto) {
    if (!state.evaluation || state.currentTeamId === null) return;
    if (state.saveInFlight) {
      state.pendingRetry = true;
      return;
    }
    if (isAuto && !state.dirty) return;

    state.saveInFlight = true;
    var payload = collectDraftPayload();
    try {
      await api("/api/judge/teams/" + state.currentTeamId + "/draft", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.dirty = false;
      state.pendingRetry = false;
      state.lastSavedAt = new Date().toLocaleTimeString();
      els.retrySaveBtn.classList.add("hidden");
      setSaveState("Draft saved at " + state.lastSavedAt);

      var team = getCurrentTeam();
      if (team && team.status !== "submitted") {
        team.status = "in_progress";
      }
      renderTeams();
      renderEvaluation();
    } catch (error) {
      state.pendingRetry = true;
      els.retrySaveBtn.classList.remove("hidden");
      setSaveState("Save failed. Waiting to retry.");
      showAlert(error.message || "Draft save failed", "error");
    } finally {
      state.saveInFlight = false;
    }
  }

  async function submitFinal() {
    if (!state.evaluation || state.currentTeamId === null) return;
    var ok = window.confirm("Submit final scores? You can edit only until deadline.");
    if (!ok) return;

    els.submitBtn.disabled = true;
    try {
      var payload = collectDraftPayload();
      await api("/api/judge/teams/" + state.currentTeamId + "/submit", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setSaveState("Submitted successfully.");
      showAlert("Final scores submitted.", "info");
      await loadTeams();
      await openEvaluation(state.currentTeamId);
    } catch (error) {
      showAlert(error.message || "Submit failed", "error");
    } finally {
      els.submitBtn.disabled = false;
    }
  }

  async function loadTeams() {
    state.teams = await api("/api/judge/teams");
    renderTeams();
  }

  async function openEvaluation(teamId) {
    state.currentTeamId = teamId;
    state.evaluation = await api("/api/judge/teams/" + teamId + "/evaluation");
    renderEvaluation();
  }

  async function login(name, password) {
    return api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ name: name, password: password }),
    });
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } finally {
      state.user = null;
      state.teams = [];
      state.currentTeamId = null;
      state.evaluation = null;
      showView("login");
      renderTeams();
      renderEvaluation();
      els.nameInput.value = "";
      els.passwordInput.value = "";
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function attachEvents() {
    els.loginForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      var name = els.nameInput.value.trim();
      var password = els.passwordInput.value;
      if (!name || !password) {
        showAlert("Name and password are required.");
        return;
      }
      try {
        var user = await login(name, password);
        if (user.role !== "judge") {
          showAlert("This screen currently supports judge role only.");
          await logout();
          return;
        }
        state.user = user;
        els.judgeName.textContent = user.name;
        showView("judge");
        await loadTeams();
      } catch (error) {
        showAlert(error.message || "Login failed");
      }
    });

    els.logoutBtn.addEventListener("click", function () {
      logout();
    });

    els.refreshTeamsBtn.addEventListener("click", async function () {
      try {
        await loadTeams();
        if (state.currentTeamId !== null) {
          await openEvaluation(state.currentTeamId);
        }
      } catch (error) {
        showAlert(error.message || "Unable to refresh");
      }
    });

    els.teamsGrid.addEventListener("click", async function (e) {
      var btn = e.target.closest("button[data-team-id]");
      if (!btn) return;
      var teamId = Number(btn.getAttribute("data-team-id"));
      try {
        await openEvaluation(teamId);
      } catch (error) {
        showAlert(error.message || "Unable to load evaluation");
      }
    });

    els.criteriaGrid.addEventListener("input", markDirtyAndSchedule);
    els.remarksInput.addEventListener("input", markDirtyAndSchedule);

    els.saveDraftBtn.addEventListener("click", function () {
      saveDraft(false);
    });

    els.retrySaveBtn.addEventListener("click", function () {
      saveDraft(false);
    });

    els.submitBtn.addEventListener("click", submitFinal);

    window.addEventListener("online", function () {
      setNetworkStatus();
      if (state.pendingRetry) {
        saveDraft(false);
      }
    });
    window.addEventListener("offline", setNetworkStatus);

    window.setInterval(function () {
      if (state.pendingRetry && navigator.onLine) {
        saveDraft(false);
      }
    }, 8000);

    window.addEventListener("beforeunload", function (event) {
      if (state.dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  async function bootstrap() {
    attachEvents();
    setNetworkStatus();
    try {
      var user = await api("/api/auth/me");
      if (user.role !== "judge") {
        showView("login");
        showAlert("Please login with a judge account.");
        return;
      }
      state.user = user;
      els.judgeName.textContent = user.name;
      showView("judge");
      await loadTeams();
    } catch (_error) {
      showView("login");
    }
  }

  bootstrap();
})();

