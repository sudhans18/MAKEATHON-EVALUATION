(function () {
  "use strict";

  var state = {
    user: null,
    admin: {
      activeTab: "overview",
      dashboardTimer: null,
      judges: [],
      teams: [],
      criteria: [],
      assignmentMap: {},
      rankings: [],
    },
    judge: {
      teams: [],
      currentTeamId: null,
      evaluation: null,
      dirty: false,
      saveTimer: null,
      saveInFlight: false,
      pendingRetry: false,
      lastSavedAt: null,
    },
  };

  var els = {
    pageTitle: document.getElementById("pageTitle"),
    networkBanner: document.getElementById("networkBanner"),
    alertBanner: document.getElementById("alertBanner"),
    sessionActions: document.getElementById("sessionActions"),
    userNamePill: document.getElementById("userNamePill"),
    logoutBtn: document.getElementById("logoutBtn"),
    loginView: document.getElementById("loginView"),
    loginForm: document.getElementById("loginForm"),
    nameInput: document.getElementById("nameInput"),
    passwordInput: document.getElementById("passwordInput"),
    judgeView: document.getElementById("judgeView"),
    adminView: document.getElementById("adminView"),
    refreshTeamsBtn: document.getElementById("refreshTeamsBtn"),
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
    adminTabList: document.getElementById("adminTabList"),
    refreshDashboardBtn: document.getElementById("refreshDashboardBtn"),
    statsGrid: document.getElementById("statsGrid"),
    overviewRankingBody: document.getElementById("overviewRankingBody"),
    judgeCreateForm: document.getElementById("judgeCreateForm"),
    judgeCreateName: document.getElementById("judgeCreateName"),
    judgeCreatePassword: document.getElementById("judgeCreatePassword"),
    judgesTableBody: document.getElementById("judgesTableBody"),
    teamCreateForm: document.getElementById("teamCreateForm"),
    teamCreateName: document.getElementById("teamCreateName"),
    teamCreateProblem: document.getElementById("teamCreateProblem"),
    teamCreateExpected: document.getElementById("teamCreateExpected"),
    teamsTableBody: document.getElementById("teamsTableBody"),
    criteriaCreateForm: document.getElementById("criteriaCreateForm"),
    criteriaCreateName: document.getElementById("criteriaCreateName"),
    criteriaCreateMax: document.getElementById("criteriaCreateMax"),
    criteriaTableBody: document.getElementById("criteriaTableBody"),
    assignmentJudgeSelect: document.getElementById("assignmentJudgeSelect"),
    assignmentTeamsList: document.getElementById("assignmentTeamsList"),
    saveAssignmentBtn: document.getElementById("saveAssignmentBtn"),
    refreshScoresBtn: document.getElementById("refreshScoresBtn"),
    scoresTableBody: document.getElementById("scoresTableBody"),
    refreshRankingsBtn: document.getElementById("refreshRankingsBtn"),
    rankingsTableBody: document.getElementById("rankingsTableBody"),
    deadlineInput: document.getElementById("deadlineInput"),
    saveDeadlineBtn: document.getElementById("saveDeadlineBtn"),
    clearDeadlineBtn: document.getElementById("clearDeadlineBtn"),
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function setBanner(el, message, type) {
    if (!message) {
      el.className = "banner hidden";
      el.textContent = "";
      return;
    }
    el.className = "banner " + (type || "info");
    el.textContent = message;
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

  function setNetworkStatus() {
    if (navigator.onLine) {
      if (state.judge.pendingRetry) {
        setBanner(els.networkBanner, "Back online. Retrying draft save...", "info");
      } else {
        setBanner(els.networkBanner, "", "");
      }
    } else {
      setBanner(els.networkBanner, "You are offline. Actions will retry when connection returns.", "error");
    }
  }

  async function api(path, options) {
    var cfg = options || {};
    var headers = cfg.headers || {};
    if (!(cfg.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    var response;
    try {
      response = await fetch(path, {
        method: cfg.method || "GET",
        headers: headers,
        body: cfg.body,
        credentials: "include",
      });
    } catch (_err) {
      setNetworkStatus();
      throw new Error("Unable to reach server");
    }
    var ct = response.headers.get("content-type") || "";
    var data = ct.indexOf("application/json") >= 0 ? await response.json() : await response.text();
    if (!response.ok) {
      throw new Error((data && data.error) || ("Request failed with status " + response.status));
    }
    return data;
  }

  function showMode(mode) {
    els.loginView.classList.toggle("hidden", mode !== "login");
    els.judgeView.classList.toggle("hidden", mode !== "judge");
    els.adminView.classList.toggle("hidden", mode !== "admin");
    els.sessionActions.classList.toggle("hidden", mode === "login");
    if (mode !== "admin") stopAdminAutoRefresh();
    if (mode === "judge") {
      els.pageTitle.textContent = "Judge Console";
    } else if (mode === "admin") {
      els.pageTitle.textContent = "Admin Dashboard";
    } else {
      els.pageTitle.textContent = "Hackathon Judging System";
    }
  }

  function statusLabel(status) {
    if (status === "in_progress") return "In Progress";
    if (status === "submitted") return "Submitted";
    return "Not Started";
  }

  function getCurrentJudgeTeam() {
    for (var i = 0; i < state.judge.teams.length; i += 1) {
      if (state.judge.teams[i].id === state.judge.currentTeamId) return state.judge.teams[i];
    }
    return null;
  }

  function setSaveState(text) {
    els.saveState.textContent = text || "";
  }

  function setJudgeDisabled(disabled) {
    var inputs = els.criteriaGrid.querySelectorAll("input");
    for (var i = 0; i < inputs.length; i += 1) inputs[i].disabled = disabled;
    els.remarksInput.disabled = disabled;
    els.saveDraftBtn.disabled = disabled;
    els.submitBtn.disabled = disabled;
  }

  function renderJudgeTeams() {
    els.teamCount.textContent = state.judge.teams.length + " teams";
    if (!state.judge.teams.length) {
      els.teamsGrid.innerHTML = "<p class='muted'>No teams assigned yet.</p>";
      return;
    }
    els.teamsGrid.innerHTML = state.judge.teams.map(function (team) {
      return "<article class='team-card'>" +
        "<div class='section-head'><h3>" + escapeHtml(team.name) + "</h3>" +
        "<span class='status-badge status-" + team.status + "'>" + statusLabel(team.status) + "</span></div>" +
        "<p>Open scoring form for this team.</p>" +
        "<button class='btn btn-secondary' data-team-id='" + team.id + "' type='button'>Open</button></article>";
    }).join("");
  }

  function renderJudgeEvaluation() {
    var evaluation = state.judge.evaluation;
    if (!evaluation) {
      els.evaluationPanel.classList.add("hidden");
      return;
    }
    els.evaluationPanel.classList.remove("hidden");
    els.evalTitle.textContent = evaluation.team.name + " Evaluation";
    var team = getCurrentJudgeTeam();
    var status = team ? team.status : "not_started";
    els.evalStatusBadge.className = "status-badge status-" + status;
    els.evalStatusBadge.textContent = statusLabel(status);
    els.problemText.textContent = evaluation.team.problem_statement || "No problem statement available.";
    els.solutionText.textContent = evaluation.team.expected_solution || "No expected solution available.";
    els.deadlineText.textContent = evaluation.submission_deadline ?
      ("Deadline: " + evaluation.submission_deadline) : "Deadline: Not set by admin";
    if (!evaluation.editable) els.deadlineText.textContent += " (Editing locked)";

    els.criteriaGrid.innerHTML = evaluation.criteria.map(function (c) {
      var v = evaluation.scores[String(c.id)];
      var value = v == null ? "" : String(v);
      return "<div class='criterion-row'><div><strong>" + escapeHtml(c.name) + "</strong>" +
        "<small>Range: 0 to " + c.max_score + "</small></div>" +
        "<input type='number' step='0.1' min='0' max='" + c.max_score + "' data-criterion-id='" + c.id + "' value='" + value + "' /></div>";
    }).join("");
    els.remarksInput.value = evaluation.remarks || "";
    state.judge.dirty = false;
    state.judge.pendingRetry = false;
    els.retrySaveBtn.classList.add("hidden");

    var isSubmitted = evaluation.submission && evaluation.submission.is_submitted === 1;
    setJudgeDisabled(!evaluation.editable || isSubmitted);
    if (isSubmitted) setSaveState("Final submission completed.");
    else if (!evaluation.editable) setSaveState("Editing locked due to submission deadline.");
    else setSaveState(state.judge.lastSavedAt ? ("Draft saved at " + state.judge.lastSavedAt) : "No unsaved changes.");
  }

  function collectJudgePayload() {
    var scores = {};
    var inputs = els.criteriaGrid.querySelectorAll("input[data-criterion-id]");
    for (var i = 0; i < inputs.length; i += 1) {
      var input = inputs[i];
      var raw = input.value.trim();
      scores[input.getAttribute("data-criterion-id")] = raw === "" ? null : Number(raw);
    }
    return { scores: scores, remarks: els.remarksInput.value || "" };
  }

  function markJudgeDirty() {
    if (!state.judge.evaluation || !state.judge.evaluation.editable) return;
    if (state.judge.evaluation.submission && state.judge.evaluation.submission.is_submitted === 1) return;
    state.judge.dirty = true;
    setSaveState("Unsaved changes...");
    if (state.judge.saveTimer) clearTimeout(state.judge.saveTimer);
    state.judge.saveTimer = window.setTimeout(function () { saveJudgeDraft(true); }, 1500);
  }

  async function saveJudgeDraft(isAuto) {
    if (!state.judge.evaluation || state.judge.currentTeamId == null) return;
    if (state.judge.saveInFlight) {
      state.judge.pendingRetry = true;
      return;
    }
    if (isAuto && !state.judge.dirty) return;
    state.judge.saveInFlight = true;
    try {
      await api("/api/judge/teams/" + state.judge.currentTeamId + "/draft", {
        method: "PUT",
        body: JSON.stringify(collectJudgePayload()),
      });
      state.judge.dirty = false;
      state.judge.pendingRetry = false;
      state.judge.lastSavedAt = new Date().toLocaleTimeString();
      els.retrySaveBtn.classList.add("hidden");
      setSaveState("Draft saved at " + state.judge.lastSavedAt);
      var team = getCurrentJudgeTeam();
      if (team && team.status !== "submitted") team.status = "in_progress";
      renderJudgeTeams();
      renderJudgeEvaluation();
    } catch (error) {
      state.judge.pendingRetry = true;
      els.retrySaveBtn.classList.remove("hidden");
      setSaveState("Save failed. Waiting to retry.");
      showAlert(error.message);
    } finally {
      state.judge.saveInFlight = false;
    }
  }

  async function submitJudgeFinal() {
    if (!state.judge.evaluation || state.judge.currentTeamId == null) return;
    if (!window.confirm("Submit final scores?")) return;
    els.submitBtn.disabled = true;
    try {
      await api("/api/judge/teams/" + state.judge.currentTeamId + "/submit", {
        method: "POST",
        body: JSON.stringify(collectJudgePayload()),
      });
      showAlert("Final scores submitted.", "info");
      await loadJudgeTeams();
      await openJudgeEvaluation(state.judge.currentTeamId);
    } catch (error) {
      showAlert(error.message);
    } finally {
      els.submitBtn.disabled = false;
    }
  }

  async function loadJudgeTeams() {
    state.judge.teams = await api("/api/judge/teams");
    renderJudgeTeams();
  }

  async function openJudgeEvaluation(teamId) {
    state.judge.currentTeamId = Number(teamId);
    state.judge.evaluation = await api("/api/judge/teams/" + teamId + "/evaluation");
    renderJudgeEvaluation();
  }

  function stopAdminAutoRefresh() {
    if (state.admin.dashboardTimer) {
      clearInterval(state.admin.dashboardTimer);
      state.admin.dashboardTimer = null;
    }
  }

  function startAdminAutoRefresh() {
    stopAdminAutoRefresh();
    state.admin.dashboardTimer = window.setInterval(function () {
      if (state.user && state.user.role === "admin" && state.admin.activeTab === "overview") {
        loadOverview();
      }
    }, 8000);
  }

  function switchAdminTab(tab) {
    state.admin.activeTab = tab;
    var btns = els.adminTabList.querySelectorAll(".tab-btn");
    for (var i = 0; i < btns.length; i += 1) {
      btns[i].classList.toggle("active", btns[i].getAttribute("data-tab") === tab);
    }
    var panels = document.querySelectorAll(".admin-tab-panel");
    for (var j = 0; j < panels.length; j += 1) panels[j].classList.add("hidden");
    var panel = document.getElementById("tab-" + tab);
    if (panel) panel.classList.remove("hidden");
    loadAdminTab(tab);
  }

  function renderOverview(dashboard) {
    var c = dashboard.counts || {};
    var stats = [
      ["Judges", c.judges || 0],
      ["Teams", c.teams || 0],
      ["Criteria", c.criteria || 0],
      ["Assignments", c.assignments || 0],
      ["Submitted", c.submitted || 0],
      ["Submission Rows", c.submission_total_records || 0],
    ];
    els.statsGrid.innerHTML = stats.map(function (s) {
      return "<article class='stat-card'><h4>" + s[0] + "</h4><p>" + s[1] + "</p></article>";
    }).join("");
    var top = (dashboard.rankings || []).slice(0, 10);
    els.overviewRankingBody.innerHTML = top.map(function (r) {
      return "<tr><td>" + r.rank + "</td><td>" + escapeHtml(r.team_name) + "</td><td>" + r.avg_total_score +
        "</td><td>" + r.submitted_judges + "</td></tr>";
    }).join("") || "<tr><td colspan='4'>No ranking data yet.</td></tr>";
  }

  async function loadOverview() {
    var dashboard = await api("/api/admin/dashboard");
    renderOverview(dashboard);
  }

  function renderJudges() {
    els.judgesTableBody.innerHTML = state.admin.judges.map(function (j) {
      return "<tr><td>" + escapeHtml(j.name) + "</td><td>" + j.assigned_teams + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-judge' data-id='" + j.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-judge' data-id='" + j.id + "' type='button'>Delete</button>" +
        "</td></tr>";
    }).join("") || "<tr><td colspan='3'>No judges found.</td></tr>";
  }

  async function loadJudges() {
    state.admin.judges = await api("/api/admin/judges");
    renderJudges();
  }

  function renderTeamsTable() {
    els.teamsTableBody.innerHTML = state.admin.teams.map(function (t) {
      return "<tr><td>" + escapeHtml(t.name) + "</td><td>" + t.assigned_judges + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-team' data-id='" + t.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-team' data-id='" + t.id + "' type='button'>Delete</button>" +
        "</td></tr>";
    }).join("") || "<tr><td colspan='3'>No teams found.</td></tr>";
  }

  async function loadTeamsAdmin() {
    state.admin.teams = await api("/api/admin/teams");
    renderTeamsTable();
  }

  function renderCriteria() {
    els.criteriaTableBody.innerHTML = state.admin.criteria.map(function (c) {
      return "<tr><td>" + escapeHtml(c.name) + "</td><td>" + c.max_score + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-criterion' data-id='" + c.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-criterion' data-id='" + c.id + "' type='button'>Delete</button>" +
        "</td></tr>";
    }).join("") || "<tr><td colspan='3'>No criteria found.</td></tr>";
  }

  async function loadCriteria() {
    state.admin.criteria = await api("/api/admin/criteria");
    renderCriteria();
  }

  async function loadAssignmentsTab() {
    var results = await Promise.all([
      api("/api/admin/judges"),
      api("/api/admin/teams"),
      api("/api/admin/assignments"),
    ]);
    state.admin.judges = results[0];
    state.admin.teams = results[1];
    var rows = results[2];
    state.admin.assignmentMap = {};
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i];
      if (!state.admin.assignmentMap[row.judge_id]) state.admin.assignmentMap[row.judge_id] = {};
      state.admin.assignmentMap[row.judge_id][row.team_id] = true;
    }
    els.assignmentJudgeSelect.innerHTML = state.admin.judges.map(function (j) {
      return "<option value='" + j.id + "'>" + escapeHtml(j.name) + "</option>";
    }).join("");
    renderAssignmentChecks();
  }

  function renderAssignmentChecks() {
    var judgeId = Number(els.assignmentJudgeSelect.value || 0);
    var assigned = state.admin.assignmentMap[judgeId] || {};
    els.assignmentTeamsList.innerHTML = state.admin.teams.map(function (t) {
      var checked = assigned[t.id] ? "checked" : "";
      return "<label class='checkbox-item'><input type='checkbox' value='" + t.id + "' " + checked + " />" +
        "<span>" + escapeHtml(t.name) + "</span></label>";
    }).join("") || "<p class='muted'>No teams available.</p>";
  }

  async function loadScores() {
    var rows = await api("/api/admin/scores");
    els.scoresTableBody.innerHTML = rows.map(function (r) {
      return "<tr><td>" + escapeHtml(r.team_name) + "</td><td>" + escapeHtml(r.judge_name) + "</td><td>" +
        escapeHtml(r.criterion_name) + "</td><td>" + r.score + " / " + r.max_score + "</td><td>" +
        (r.is_submitted ? "Yes" : "No") + "</td></tr>";
    }).join("") || "<tr><td colspan='5'>No scores yet.</td></tr>";
  }

  function renderRankings() {
    els.rankingsTableBody.innerHTML = state.admin.rankings.map(function (r) {
      var overrideRank = r.override_rank == null ? "" : r.override_rank;
      var reason = r.override_reason || "";
      return "<tr data-team-id='" + r.team_id + "'><td>" + r.rank + "</td><td>" + escapeHtml(r.team_name) + "</td><td>" +
        r.avg_total_score + "</td><td>" + r.secondary_score + "</td><td>" +
        "<div class='form-inline'>" +
        "<input data-override-rank value='" + overrideRank + "' placeholder='Rank' style='max-width:90px' />" +
        "<input data-override-reason value='" + escapeHtml(reason) + "' placeholder='Reason' />" +
        "<button class='btn btn-secondary' data-action='save-override' type='button'>Save</button>" +
        "<button class='btn btn-danger' data-action='clear-override' type='button'>Clear</button>" +
        "</div></td></tr>";
    }).join("") || "<tr><td colspan='5'>No rankings available.</td></tr>";
  }

  async function loadRankings() {
    state.admin.rankings = await api("/api/admin/rankings");
    renderRankings();
  }

  function isoToLocalInput(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  async function loadDeadline() {
    var row = await api("/api/admin/settings/submission-deadline");
    els.deadlineInput.value = isoToLocalInput(row.submission_deadline);
  }

  async function loadAdminTab(tab) {
    try {
      if (tab === "overview") await loadOverview();
      if (tab === "judges") await loadJudges();
      if (tab === "teams") await loadTeamsAdmin();
      if (tab === "criteria") await loadCriteria();
      if (tab === "assignments") await loadAssignmentsTab();
      if (tab === "scores") await loadScores();
      if (tab === "rankings") await loadRankings();
      if (tab === "settings") await loadDeadline();
    } catch (error) {
      showAlert(error.message);
    }
  }

  async function login(name, password) {
    return api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ name: name, password: password }),
    });
  }

  async function logout() {
    stopAdminAutoRefresh();
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } catch (_error) {}
    state.user = null;
    state.judge.teams = [];
    state.judge.currentTeamId = null;
    state.judge.evaluation = null;
    showMode("login");
  }

  function attachCommonEvents() {
    els.loginForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      var name = els.nameInput.value.trim();
      var password = els.passwordInput.value;
      if (!name || !password) return showAlert("Name and password are required.");
      try {
        state.user = await login(name, password);
        els.userNamePill.textContent = state.user.name + " (" + state.user.role + ")";
        if (state.user.role === "judge") {
          showMode("judge");
          await loadJudgeTeams();
        } else if (state.user.role === "admin") {
          showMode("admin");
          switchAdminTab("overview");
          startAdminAutoRefresh();
        } else {
          showMode("login");
          showAlert("Unsupported role.");
        }
      } catch (error) {
        showAlert(error.message);
      }
    });

    els.logoutBtn.addEventListener("click", logout);
    window.addEventListener("online", function () {
      setNetworkStatus();
      if (state.judge.pendingRetry) saveJudgeDraft(false);
    });
    window.addEventListener("offline", setNetworkStatus);
  }

  function attachJudgeEvents() {
    els.refreshTeamsBtn.addEventListener("click", async function () {
      try {
        await loadJudgeTeams();
        if (state.judge.currentTeamId != null) await openJudgeEvaluation(state.judge.currentTeamId);
      } catch (error) {
        showAlert(error.message);
      }
    });
    els.teamsGrid.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-team-id]");
      if (!btn) return;
      try {
        await openJudgeEvaluation(btn.getAttribute("data-team-id"));
      } catch (error) {
        showAlert(error.message);
      }
    });
    els.criteriaGrid.addEventListener("input", markJudgeDirty);
    els.remarksInput.addEventListener("input", markJudgeDirty);
    els.saveDraftBtn.addEventListener("click", function () { saveJudgeDraft(false); });
    els.retrySaveBtn.addEventListener("click", function () { saveJudgeDraft(false); });
    els.submitBtn.addEventListener("click", submitJudgeFinal);
    window.setInterval(function () {
      if (state.judge.pendingRetry && navigator.onLine) saveJudgeDraft(false);
    }, 8000);
  }

  function attachAdminEvents() {
    els.adminTabList.addEventListener("click", function (event) {
      var btn = event.target.closest(".tab-btn");
      if (!btn) return;
      switchAdminTab(btn.getAttribute("data-tab"));
    });
    els.refreshDashboardBtn.addEventListener("click", function () { loadOverview().catch(function (e) { showAlert(e.message); }); });

    els.judgeCreateForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        await api("/api/admin/judges", {
          method: "POST",
          body: JSON.stringify({ name: els.judgeCreateName.value.trim(), password: els.judgeCreatePassword.value }),
        });
        els.judgeCreateForm.reset();
        await loadJudges();
        showAlert("Judge created.", "info");
      } catch (error) { showAlert(error.message); }
    });
    els.judgesTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var id = Number(btn.getAttribute("data-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "delete-judge") {
          if (!window.confirm("Delete this judge?")) return;
          await api("/api/admin/judges/" + id, { method: "DELETE" });
          await loadJudges();
        }
        if (action === "edit-judge") {
          var name = window.prompt("New judge name:");
          var password = window.prompt("New password (leave blank to keep current):");
          var payload = {};
          if (name && name.trim()) payload.name = name.trim();
          if (password) payload.password = password;
          if (!Object.keys(payload).length) return;
          await api("/api/admin/judges/" + id, { method: "PUT", body: JSON.stringify(payload) });
          await loadJudges();
        }
      } catch (error) { showAlert(error.message); }
    });

    els.teamCreateForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        await api("/api/admin/teams", {
          method: "POST",
          body: JSON.stringify({
            name: els.teamCreateName.value.trim(),
            problem_statement: els.teamCreateProblem.value,
            expected_solution: els.teamCreateExpected.value,
          }),
        });
        els.teamCreateForm.reset();
        await loadTeamsAdmin();
        showAlert("Team created.", "info");
      } catch (error) { showAlert(error.message); }
    });
    els.teamsTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var id = Number(btn.getAttribute("data-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "delete-team") {
          if (!window.confirm("Delete this team?")) return;
          await api("/api/admin/teams/" + id, { method: "DELETE" });
          await loadTeamsAdmin();
        }
        if (action === "edit-team") {
          var existing = null;
          for (var i = 0; i < state.admin.teams.length; i += 1) if (state.admin.teams[i].id === id) existing = state.admin.teams[i];
          var name = window.prompt("Team name:", existing ? existing.name : "");
          if (name == null) return;
          var ps = window.prompt("Problem statement:", existing ? existing.problem_statement : "");
          if (ps == null) return;
          var es = window.prompt("Expected solution:", existing ? existing.expected_solution : "");
          if (es == null) return;
          await api("/api/admin/teams/" + id, {
            method: "PUT",
            body: JSON.stringify({ name: name.trim(), problem_statement: ps, expected_solution: es }),
          });
          await loadTeamsAdmin();
        }
      } catch (error) { showAlert(error.message); }
    });

    els.criteriaCreateForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        await api("/api/admin/criteria", {
          method: "POST",
          body: JSON.stringify({ name: els.criteriaCreateName.value.trim(), max_score: Number(els.criteriaCreateMax.value) }),
        });
        els.criteriaCreateForm.reset();
        await loadCriteria();
      } catch (error) { showAlert(error.message); }
    });
    els.criteriaTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var id = Number(btn.getAttribute("data-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "delete-criterion") {
          if (!window.confirm("Delete this criterion?")) return;
          await api("/api/admin/criteria/" + id, { method: "DELETE" });
          await loadCriteria();
        }
        if (action === "edit-criterion") {
          var existing = null;
          for (var i = 0; i < state.admin.criteria.length; i += 1) if (state.admin.criteria[i].id === id) existing = state.admin.criteria[i];
          var name = window.prompt("Criterion name:", existing ? existing.name : "");
          if (name == null) return;
          var max = window.prompt("Max score:", existing ? existing.max_score : "");
          if (max == null) return;
          await api("/api/admin/criteria/" + id, { method: "PUT", body: JSON.stringify({ name: name.trim(), max_score: Number(max) }) });
          await loadCriteria();
        }
      } catch (error) { showAlert(error.message); }
    });

    els.assignmentJudgeSelect.addEventListener("change", renderAssignmentChecks);
    els.saveAssignmentBtn.addEventListener("click", async function () {
      var judgeId = Number(els.assignmentJudgeSelect.value || 0);
      var boxes = els.assignmentTeamsList.querySelectorAll("input[type='checkbox']");
      var ids = [];
      for (var i = 0; i < boxes.length; i += 1) if (boxes[i].checked) ids.push(Number(boxes[i].value));
      try {
        await api("/api/admin/assignments/" + judgeId, { method: "PUT", body: JSON.stringify({ team_ids: ids }) });
        showAlert("Assignments saved.", "info");
        await loadAssignmentsTab();
      } catch (error) { showAlert(error.message); }
    });

    els.refreshScoresBtn.addEventListener("click", function () { loadScores().catch(function (e) { showAlert(e.message); }); });
    els.refreshRankingsBtn.addEventListener("click", function () { loadRankings().catch(function (e) { showAlert(e.message); }); });
    els.rankingsTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var row = btn.closest("tr[data-team-id]");
      if (!row) return;
      var teamId = Number(row.getAttribute("data-team-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "save-override") {
          var rankInput = row.querySelector("input[data-override-rank]");
          var reasonInput = row.querySelector("input[data-override-reason]");
          var rank = Number((rankInput.value || "").trim());
          if (!rank || rank < 1) return showAlert("Enter a valid override rank.");
          await api("/api/admin/rankings/override", {
            method: "PUT",
            body: JSON.stringify({ team_id: teamId, override_rank: rank, reason: reasonInput.value || "" }),
          });
          await loadRankings();
        }
        if (action === "clear-override") {
          await api("/api/admin/rankings/override/" + teamId, { method: "DELETE" });
          await loadRankings();
        }
      } catch (error) { showAlert(error.message); }
    });

    els.saveDeadlineBtn.addEventListener("click", async function () {
      try {
        var value = els.deadlineInput.value ? (els.deadlineInput.value + ":00") : null;
        await api("/api/admin/settings/submission-deadline", {
          method: "PUT",
          body: JSON.stringify({ submission_deadline: value }),
        });
        showAlert("Deadline saved.", "info");
      } catch (error) { showAlert(error.message); }
    });
    els.clearDeadlineBtn.addEventListener("click", async function () {
      try {
        await api("/api/admin/settings/submission-deadline", {
          method: "PUT",
          body: JSON.stringify({ submission_deadline: null }),
        });
        els.deadlineInput.value = "";
        showAlert("Deadline cleared.", "info");
      } catch (error) { showAlert(error.message); }
    });
  }

  async function bootstrap() {
    attachCommonEvents();
    attachJudgeEvents();
    attachAdminEvents();
    setNetworkStatus();
    try {
      state.user = await api("/api/auth/me");
      els.userNamePill.textContent = state.user.name + " (" + state.user.role + ")";
      if (state.user.role === "judge") {
        showMode("judge");
        await loadJudgeTeams();
      } else if (state.user.role === "admin") {
        showMode("admin");
        switchAdminTab("overview");
        startAdminAutoRefresh();
      } else {
        showMode("login");
      }
    } catch (_error) {
      showMode("login");
    }
  }

  bootstrap();
})();

