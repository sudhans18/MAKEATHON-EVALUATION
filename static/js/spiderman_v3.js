(function () {
  "use strict";
  console.log("Spider-Verse Judging Matrix v2.2 Loaded");

  var state = {
    user: null,
    rounds: [],
    activeRoundId: null,
    admin: {
      activeTab: "overview",
      dashboardTimer: null,
      judges: [],
      teams: [],
      criteria: [],
      rankings: [],
      assignmentMap: {},
    },
    judge: {
      teams: [],
      currentTeamId: null,
      evaluation: null,
      dirty: false,
      saveTimer: null,
      pendingRetry: false,
      lastSavedAt: null,
      saveInFlight: false,
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
    judgeRoundSelect: document.getElementById("judgeRoundSelect"),
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
    adminRoundSelect: document.getElementById("adminRoundSelect"),
    adminTabList: document.getElementById("adminTabList"),
    refreshDashboardBtn: document.getElementById("refreshDashboardBtn"),
    statsGrid: document.getElementById("statsGrid"),
    overviewRankingBody: document.getElementById("overviewRankingBody"),
    roundCreateForm: document.getElementById("roundCreateForm"),
    roundCreateName: document.getElementById("roundCreateName"),
    roundCreateSequence: document.getElementById("roundCreateSequence"),
    roundsTableBody: document.getElementById("roundsTableBody"),
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
    rankingRoundSelect: document.getElementById("rankingRoundSelect"),
    rankingCategorySelect: document.getElementById("rankingCategorySelect"),
    exportRankingsBtn: document.getElementById("exportRankingsBtn"),
    rankColAvgTotal: document.getElementById("rankColAvgTotal"),
    deadlineInput: document.getElementById("deadlineInput"),
    saveDeadlineBtn: document.getElementById("saveDeadlineBtn"),
    clearDeadlineBtn: document.getElementById("clearDeadlineBtn"),
    teamCreatePsId: document.getElementById("teamCreatePsId"),
    teamCreateCategory: document.getElementById("teamCreateCategory"),
    assignmentTeamsListHW: document.getElementById("assignmentTeamsListHW"),
    assignmentTeamsListSW: document.getElementById("assignmentTeamsListSW"),
    remarksModal: document.getElementById("remarksModal"),
    modalRemarksList: document.getElementById("modalRemarksList"),
    modalTeamName: document.getElementById("modalTeamName"),
    teamInfoModal: document.getElementById("teamInfoModal"),
    infoTeamName: document.getElementById("infoTeamName"),
    infoPsId: document.getElementById("infoPsId"),
    infoCategory: document.getElementById("infoCategory"),
    infoProblem: document.getElementById("infoProblem"),
    infoExpected: document.getElementById("infoExpected"),
  };


  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function draftKey(roundId, teamId) {
    if (!state.user) return "";
    return "draft_" + state.user.id + "_" + roundId + "_" + teamId;
  }

  function saveLocalDraft(roundId, teamId, payload) {
    try {
      window.localStorage.setItem(
        draftKey(roundId, teamId),
        JSON.stringify({
          scores: payload.scores || {},
          remarks: payload.remarks || "",
          ts: new Date().toISOString(),
        })
      );
    } catch (_err) {}
  }

  function readLocalDraft(roundId, teamId) {
    try {
      var raw = window.localStorage.getItem(draftKey(roundId, teamId));
      return raw ? JSON.parse(raw) : null;
    } catch (_err) {
      return null;
    }
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
        if (els.alertBanner.textContent === message) setBanner(els.alertBanner, "", "");
      }, 4500);
    }
  }

  function setNetworkStatus() {
    if (navigator.onLine) {
      setBanner(els.networkBanner, "", "");
    } else {
      setBanner(els.networkBanner, "You are offline. Changes will retry when connection returns.", "error");
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
      throw new Error("Unable to reach server");
    }
    var ct = response.headers.get("content-type") || "";
    var data = ct.indexOf("application/json") >= 0 ? await response.json() : await response.text();
    if (!response.ok) {
      throw new Error((data && data.error) || ("Request failed: " + response.status));
    }
    return data;
  }

  function roundQuery() {
    return state.activeRoundId ? ("?round_id=" + state.activeRoundId) : "";
  }

  function showMode(mode) {
    const isLogin = mode === "login";
    els.loginView.classList.toggle("hidden", mode !== "login");
    els.judgeView.classList.toggle("hidden", mode !== "judge");
    els.adminView.classList.toggle("hidden", mode !== "admin");
    
    // Toggle Landing Page Extras
    const landingExtras = document.getElementById("landingExtras");
    const loginWrapper = document.getElementById("loginWrapper");
    const mainContent = document.getElementById("mainContent");
    const sessionActions = document.getElementById("sessionActions");

    if (landingExtras) landingExtras.classList.toggle("hidden", !isLogin);
    if (loginWrapper) loginWrapper.classList.toggle("hidden", !isLogin);
    if (mainContent) mainContent.classList.toggle("hidden", isLogin);
    if (sessionActions) sessionActions.classList.toggle("hidden", isLogin);

    if (mode === "judge") {
        document.getElementById("pageTitle").textContent = "Judge Console";
    } else if (mode === "admin") {
        document.getElementById("pageTitle").textContent = "Admin Dashboard";
    }

    if (mode !== "admin") stopAdminAutoRefresh();
    window.lucide && window.lucide.createIcons();
  }

  function statusLabel(status) {
    if (status === "submitted") return "Submitted";
    if (status === "in_progress") return "In Progress";
    return "Not Started";
  }

  function renderRoundSelectors() {
    var options = state.rounds.map(function (r) {
      var selected = Number(state.activeRoundId) === Number(r.id) ? "selected" : "";
      return "<option value='" + r.id + "' " + selected + ">" + escapeHtml(r.name) + "</option>";
    }).join("");
    els.adminRoundSelect.innerHTML = options;
    els.judgeRoundSelect.innerHTML = options;
  }

  async function loadRounds() {
    state.rounds = await api("/api/rounds");
    if (!state.rounds.length) return;
    if (!state.activeRoundId) state.activeRoundId = state.rounds[0].id;
    var stillExists = false;
    for (var i = 0; i < state.rounds.length; i += 1) {
      if (Number(state.rounds[i].id) === Number(state.activeRoundId)) stillExists = true;
    }
    if (!stillExists) state.activeRoundId = state.rounds[0].id;
    renderRoundSelectors();
  }

  async function persistActiveRound(roundId) {
    state.activeRoundId = Number(roundId);
    renderRoundSelectors();
    if (state.user && state.user.role === "admin") {
      await api("/api/admin/settings/active-round", {
        method: "PUT",
        body: JSON.stringify({ round_id: state.activeRoundId }),
      });
    }
  }

  function renderJudgeTeams() {
    els.teamCount.textContent = state.judge.teams.length + " teams";
    if (!state.judge.teams.length) {
      els.teamsGrid.innerHTML = "<p class='muted'>No teams assigned for this round.</p>";
      return;
    }
    els.teamsGrid.innerHTML = state.judge.teams.map(function (team) {
      var statusClass = team.is_submitted ? "submitted" : (team.draft_score_count > 0 ? "in_progress" : "not_started");
      var statusText = team.is_submitted ? "Submitted" : (team.draft_score_count > 0 ? "Draft" : "Pending");
      var catClass = team.category === "HW" ? "cat-hw" : "cat-sw";
      
      return "<article class='team-card'>" +
        "<div class='section-head'>" +
          "<div>" +
            "<h3>" + escapeHtml(team.name) + "</h3>" +
            "<code class='muted' style='font-size:0.75rem;'>" + escapeHtml(team.ps_id) + "</code> " +
            "<span class='cat-chip " + catClass + "'>" + team.category + "</span>" +
          "</div>" +
          "<span class='status-badge status-" + statusClass + "'>" + statusText + "</span>" +
        "</div>" +
        "<button class='btn btn-primary' data-team-id='" + team.id + "' type='button' style='width:100%; justify-content:center;'>" +
          "<i data-lucide='clipboard-check' style='width:16px;'></i> Evaluate" +
        "</button></article>";
    }).join("");
    if (window.lucide) window.lucide.createIcons();
  }



  function setJudgeDisabled(disabled) {
    var inputs = els.criteriaGrid.querySelectorAll("input");
    for (var i = 0; i < inputs.length; i += 1) inputs[i].disabled = disabled;
    els.remarksInput.disabled = disabled;
    els.saveDraftBtn.disabled = disabled;
    els.submitBtn.disabled = disabled;
  }

  function setSaveState(msg) {
    els.saveState.textContent = msg || "";
  }

  function collectJudgePayload() {
    var scores = {};
    var inputs = els.criteriaGrid.querySelectorAll("input[data-criterion-id]");
    for (var i = 0; i < inputs.length; i += 1) {
      var input = inputs[i];
      var raw = input.value.trim();
      scores[input.getAttribute("data-criterion-id")] = raw === "" ? null : Number(raw);
    }
    return { round_id: state.activeRoundId, scores: scores, remarks: els.remarksInput.value || "" };
  }

  function renderJudgeEvaluation() {
    var evaluation = state.judge.evaluation;
    if (!evaluation) {
      els.evaluationPanel.classList.add("hidden");
      return;
    }
    els.evaluationPanel.classList.remove("hidden");
    els.evalTitle.textContent = evaluation.team.name + " Evaluation";
    els.evalStatusBadge.className = "status-badge status-" + (evaluation.submission && evaluation.submission.is_submitted ? "submitted" : "in_progress");
    els.evalStatusBadge.textContent = evaluation.submission && evaluation.submission.is_submitted ? "Submitted" : "In Progress";
    els.problemText.textContent = evaluation.team.problem_statement || "No problem statement available.";
    els.solutionText.textContent = evaluation.team.expected_solution || "No expected solution available.";
    els.deadlineText.textContent = evaluation.submission_deadline ? ("Deadline: " + evaluation.submission_deadline) : "Deadline: Not set by admin";
    if (!evaluation.editable) els.deadlineText.textContent += " (Editing locked)";

    els.criteriaGrid.innerHTML = (evaluation.criteria || []).map(function (c) {
      var v = evaluation.scores[String(c.id)];
      var value = v == null ? "" : String(v);
      return "<div class='criterion-row'><div><strong>" + escapeHtml(c.name) + "</strong><small>Range: 0 to " + c.max_score + "</small></div>" +
        "<input type='number' step='0.1' min='0' max='" + c.max_score + "' data-criterion-id='" + c.id + "' value='" + value + "' /></div>";
    }).join("");
    els.remarksInput.value = evaluation.remarks || "";
    state.judge.dirty = false;
    setJudgeDisabled(!evaluation.editable);
    setSaveState(evaluation.editable ? "No unsaved changes." : "Editing locked.");
    if (window.lucide) window.lucide.createIcons();
  }

  async function loadJudgeTeams() {

    var payload = await api("/api/judge/teams" + roundQuery());
    state.judge.teams = payload.teams || [];
    renderJudgeTeams();
  }

  async function openJudgeEvaluation(teamId) {
    state.judge.currentTeamId = Number(teamId);
    var local = readLocalDraft(state.activeRoundId, teamId);
    if (local) {
      setSaveState("Local draft loaded.");
    }
    var evaluation = await api("/api/judge/teams/" + teamId + "/evaluation" + roundQuery());
    if (local && evaluation.submission && evaluation.submission.is_submitted !== 1) {
      evaluation.scores = local.scores || evaluation.scores || {};
      evaluation.remarks = local.remarks || evaluation.remarks || "";
    }
    state.judge.evaluation = evaluation;
    renderJudgeEvaluation();
  }

  async function saveJudgeDraft(auto) {
    if (!state.judge.evaluation || state.judge.currentTeamId == null) return;
    if (auto && !state.judge.dirty) return;
    if (state.judge.saveInFlight) return;
    state.judge.saveInFlight = true;
    var payload = collectJudgePayload();
    saveLocalDraft(state.activeRoundId, state.judge.currentTeamId, payload);
    try {
      await api("/api/judge/teams/" + state.judge.currentTeamId + "/draft", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.judge.dirty = false;
      state.judge.pendingRetry = false;
      state.judge.lastSavedAt = new Date().toLocaleTimeString();
      els.retrySaveBtn.classList.add("hidden");
      setSaveState("Draft saved at " + state.judge.lastSavedAt);
      await loadJudgeTeams();
    } catch (error) {
      state.judge.pendingRetry = true;
      els.retrySaveBtn.classList.remove("hidden");
      setSaveState("Saved locally. Retry pending.");
      showAlert(error.message);
    } finally {
      state.judge.saveInFlight = false;
    }
  }

  async function submitJudgeFinal() {
    if (!state.judge.evaluation || state.judge.currentTeamId == null) return;
    if (!window.confirm("Submit final scores for this round?")) return;
    var payload = collectJudgePayload();
    saveLocalDraft(state.activeRoundId, state.judge.currentTeamId, payload);
    try {
      await api("/api/judge/teams/" + state.judge.currentTeamId + "/submit", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showAlert("Final submission completed.", "info");
      await loadJudgeTeams();
      await openJudgeEvaluation(state.judge.currentTeamId);
    } catch (error) {
      showAlert(error.message);
    }
  }

  function markJudgeDirty() {
    if (!state.judge.evaluation || !state.judge.evaluation.editable) return;
    state.judge.dirty = true;
    setSaveState("Unsaved changes...");
    var payload = collectJudgePayload();
    saveLocalDraft(state.activeRoundId, state.judge.currentTeamId, payload);
    if (state.judge.saveTimer) clearTimeout(state.judge.saveTimer);
    state.judge.saveTimer = window.setTimeout(function () {
      saveJudgeDraft(true);
    }, 1500);
  }

  function renderOverview(dashboard) {
    var c = dashboard.counts || {};
    var stats = [
      ["Judges", c.judges || 0],
      ["Teams", c.teams || 0],
      ["Criteria", c.criteria || 0],
      ["Assignments", c.assignments || 0],
      ["Submitted", c.submitted || 0],
    ];
    els.statsGrid.innerHTML = stats.map(function (s) {
      return "<article class='stat-card'><h4>" + s[0] + "</h4><p>" + s[1] + "</p></article>";
    }).join("");
    var rows = dashboard.rankings || [];
    els.overviewRankingBody.innerHTML = rows.slice(0, 10).map(function (r) {
      return "<tr><td>" + r.rank + "</td><td>" + escapeHtml(r.team_name) + "</td><td>" + escapeHtml(r.category || "SW") + "</td><td>" + (r.avg_percentage || 0) + "%</td><td>" + (r.submitted_judges || 0) + "</td></tr>";
    }).join("") || "<tr><td colspan='5'>No ranking data yet.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }



  async function loadOverview() {
    var dashboard = await api("/api/admin/dashboard" + roundQuery());
    renderOverview(dashboard);
  }

  function renderRoundsTable() {
    els.roundsTableBody.innerHTML = state.rounds.map(function (r) {
      return "<tr><td>" + escapeHtml(r.name) + "</td><td>" + r.sequence + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-round' data-id='" + r.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-round' data-id='" + r.id + "' type='button'>Delete</button></td></tr>";
    }).join("") || "<tr><td colspan='3'>No rounds configured.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }


  function renderJudges() {
    els.judgesTableBody.innerHTML = state.admin.judges.map(function (j) {
      return "<tr><td>" + escapeHtml(j.name) + "</td><td>" + j.assigned_teams + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-judge' data-id='" + j.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-judge' data-id='" + j.id + "' type='button'>Delete</button></td></tr>";
    }).join("") || "<tr><td colspan='3'>No judges found.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }



  function renderTeamsTable() {
    els.teamsTableBody.innerHTML = state.admin.teams.map(function (t) {
      var catClass = t.category === "HW" ? "cat-hw" : "cat-sw";
      var teamNameHtml = "<a href='#' class='team-link' data-action='view-info' data-id='" + t.id + "'>" + escapeHtml(t.name) + "</a>";
      return "<tr>" +
        "<td><code class='muted'>" + escapeHtml(t.ps_id || "N/A") + "</code></td>" +
        "<td><strong>" + teamNameHtml + "</strong></td>" +
        "<td><span class='cat-chip " + catClass + "'>" + t.category + "</span></td>" +
        "<td>" + t.assigned_judges + "</td>" +
        "<td><button class='btn btn-secondary btn-sm' data-action='view-remarks' data-id='" + t.id + "' data-name='" + escapeHtml(t.name) + "' type='button'><i data-lucide='message-square' style='width:14px;'></i> Remarks</button></td>" +
        "<td>" +
        "<button class='btn btn-secondary' data-action='edit-team' data-id='" + t.id + "' type='button'><i data-lucide='edit' style='width:14px;'></i></button> " +
        "<button class='btn btn-danger' data-action='delete-team' data-id='" + t.id + "' type='button'><i data-lucide='trash' style='width:14px;'></i></button></td></tr>";
    }).join("") || "<tr><td colspan='6'>No multiversal variants found.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }


  function renderCriteria() {
    els.criteriaTableBody.innerHTML = state.admin.criteria.map(function (c) {
      return "<tr><td>" + escapeHtml(c.name) + "</td><td>" + c.max_score + "</td><td>" +
        "<button class='btn btn-secondary' data-action='edit-criterion' data-id='" + c.id + "' type='button'>Edit</button> " +
        "<button class='btn btn-danger' data-action='delete-criterion' data-id='" + c.id + "' type='button'>Delete</button></td></tr>";
    }).join("") || "<tr><td colspan='3'>No criteria for this round.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }



  function renderAssignmentChecks() {
    var judgeId = Number(els.assignmentJudgeSelect.value || 0);
    var assigned = state.admin.assignmentMap[judgeId] || {};
    console.log("Rendering assignments for judge:", judgeId, "Teams in state:", state.admin.teams.length);
    
    function makeList(filterCat) {
      var filtered = state.admin.teams.filter(function(t) { return t.category === filterCat; });
      console.log("Category:", filterCat, "Found:", filtered.length);
      return filtered.map(function (t) {
          var checked = assigned[t.id] ? "checked" : "";
          return "<label class='checkbox-item'><input type='checkbox' value='" + t.id + "' " + checked + " />" +
            "<span>" + escapeHtml(t.name) + " <small class='muted'>(" + escapeHtml(t.ps_id) + ")</small></span></label>";
        }).join("") || "<p class='muted'>None</p>";
    }

    els.assignmentTeamsListHW.innerHTML = makeList("HW");
    els.assignmentTeamsListSW.innerHTML = makeList("SW");
    if (window.lucide) window.lucide.createIcons();
  }

  function renderRankings() {
    var isOverall = els.rankingRoundSelect.value === "overall";
    els.rankColAvgTotal.classList.toggle("hidden", isOverall);
    
    els.rankingsTableBody.innerHTML = state.admin.rankings.map(function (r) {
      var override = r.override_rank == null ? "" : r.override_rank;
      var reason = r.override_reason || "";
      var category = escapeHtml(r.category || "SW");
      
      var scoreTd = isOverall ? "" : ("<td>" + r.avg_total_score + "</td>");
      
      return "<tr data-team-id='" + r.team_id + "'><td>" + r.rank + "</td><td>" + escapeHtml(r.team_name) + "</td><td>" + category + "</td>" + scoreTd + "<td>" + r.avg_percentage + "%</td><td>" +
        "<div class='form-inline'><input data-override-rank value='" + override + "' placeholder='Rank' style='max-width:90px' " + (isOverall ? "disabled" : "") + " />" +
        "<input data-override-reason value='" + escapeHtml(reason) + "' placeholder='Reason' " + (isOverall ? "disabled" : "") + " />" +
        "<button class='btn btn-secondary' data-action='save-override' type='button' " + (isOverall ? "disabled" : "") + ">Save</button>" +
        "<button class='btn btn-danger' data-action='clear-override' type='button' " + (isOverall ? "disabled" : "") + ">Clear</button></div></td></tr>";
    }).join("") || "<tr><td colspan='6'>No rankings available.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }

  function isoToLocalInput(iso) {

    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  async function loadJudges() {
    state.admin.judges = await api("/api/admin/judges" + roundQuery());
    renderJudges();
  }

  async function loadTeamsAdmin() {
    state.admin.teams = await api("/api/admin/teams" + roundQuery());
    renderTeamsTable();
  }

  async function loadCriteria() {
    state.admin.criteria = await api("/api/admin/criteria" + roundQuery());
    renderCriteria();
  }

  async function loadAssignments() {
    var results = await Promise.all([
      api("/api/admin/judges" + roundQuery()),
      api("/api/admin/teams" + roundQuery()),
      api("/api/admin/assignments" + roundQuery()),
    ]);
    state.admin.judges = results[0];
    state.admin.teams = results[1];
    state.admin.assignmentMap = {};
    var rows = results[2];
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i];
      if (!state.admin.assignmentMap[row.judge_id]) state.admin.assignmentMap[row.judge_id] = {};
      state.admin.assignmentMap[row.judge_id][row.team_id] = true;
    }
    els.assignmentJudgeSelect.innerHTML = state.admin.judges.map(function (j) {
      return "<option value='" + j.id + "'>" + escapeHtml(j.name) + "</option>";
    }).join("");
    if (state.admin.judges.length > 0 && !els.assignmentJudgeSelect.value) {
      els.assignmentJudgeSelect.selectedIndex = 0;
    }
    renderAssignmentChecks();
  }

  async function loadScores() {
    var rows = await api("/api/admin/scores" + roundQuery());
    els.scoresTableBody.innerHTML = rows.map(function (r) {
      return "<tr><td>" + escapeHtml(r.team_name) + "</td><td>" + escapeHtml(r.team_category || "SW") + "</td><td>" + escapeHtml(r.judge_name) + "</td><td>" + escapeHtml(r.criterion_name) + "</td><td>" + r.score + " / " + r.max_score + "</td><td>" + (r.is_submitted ? "Yes" : "No") + "</td><td><small>" + escapeHtml(r.remarks) + "</small></td></tr>";
    }).join("") || "<tr><td colspan='7'>No scores yet.</td></tr>";
    if (window.lucide) window.lucide.createIcons();
  }

  async function loadRankings() {

    var rid = els.rankingRoundSelect.value === "active" ? state.activeRoundId : "overall";
    var cat = els.rankingCategorySelect.value;
    var query = "?round_id=" + rid;
    if (cat !== "All") query += "&category=" + cat;
    
    var payload = await api("/api/admin/rankings" + query);
    state.admin.rankings = payload.rows || [];
    els.exportRankingsBtn.href = "/api/admin/export/csv" + query;
    renderRankings();
  }


  async function loadDeadline() {
    var payload = await api("/api/admin/settings/submission-deadline");
    els.deadlineInput.value = isoToLocalInput(payload.submission_deadline);
  }

  async function loadAdminTab(tab) {
    if (tab === "overview") return loadOverview();
    if (tab === "rounds") return Promise.resolve(renderRoundsTable());
    if (tab === "judges") return loadJudges();
    if (tab === "teams") return loadTeamsAdmin();
    if (tab === "criteria") return loadCriteria();
    if (tab === "assignments") return loadAssignments();
    if (tab === "scores") return loadScores();
    if (tab === "rankings") return loadRankings();
    if (tab === "settings") return loadDeadline();
    return Promise.resolve();
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
    loadAdminTab(tab).catch(function (error) { showAlert(error.message); });
  }

  function startAdminAutoRefresh() {
    stopAdminAutoRefresh();
    state.admin.dashboardTimer = window.setInterval(function () {
      if (state.user && state.user.role === "admin" && state.admin.activeTab === "overview") {
        loadOverview().catch(function (_e) {});
      }
    }, 8000);
  }

  function stopAdminAutoRefresh() {
    if (state.admin.dashboardTimer) {
      clearInterval(state.admin.dashboardTimer);
      state.admin.dashboardTimer = null;
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
    } catch (_err) {}
    state.user = null;
    showMode("login");
  }

  function attachCommonEvents() {
    els.loginForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        state.user = await login(els.nameInput.value.trim(), els.passwordInput.value);
        await loadRounds();
        var pillSpan = els.userNamePill.querySelector("span");
        if (pillSpan) pillSpan.textContent = state.user.name + " (" + state.user.role + ")";
        else els.userNamePill.textContent = state.user.name + " (" + state.user.role + ")";
        if (state.user.role === "judge") {

          showMode("judge");
          await loadJudgeTeams();
        } else if (state.user.role === "admin") {
          showMode("admin");
          switchAdminTab("overview");
          startAdminAutoRefresh();
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
    els.judgeRoundSelect.addEventListener("change", async function () {
      try {
        await persistActiveRound(els.judgeRoundSelect.value);
        state.judge.currentTeamId = null;
        state.judge.evaluation = null;
        renderJudgeEvaluation();
        await loadJudgeTeams();
      } catch (error) {
        showAlert(error.message);
      }
    });
    els.refreshTeamsBtn.addEventListener("click", function () {
      loadJudgeTeams().catch(function (error) { showAlert(error.message); });
    });
    els.teamsGrid.addEventListener("click", function (event) {
      var btn = event.target.closest("button[data-team-id]");
      if (!btn) return;
      openJudgeEvaluation(btn.getAttribute("data-team-id")).catch(function (error) { showAlert(error.message); });
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
    els.adminRoundSelect.addEventListener("change", async function () {
      try {
        await persistActiveRound(els.adminRoundSelect.value);
        await loadAdminTab(state.admin.activeTab);
      } catch (error) {
        showAlert(error.message);
      }
    });
    els.adminTabList.addEventListener("click", function (event) {
      var btn = event.target.closest(".tab-btn");
      if (!btn) return;
      switchAdminTab(btn.getAttribute("data-tab"));
    });
    els.refreshDashboardBtn.addEventListener("click", function () {
      loadOverview().catch(function (error) { showAlert(error.message); });
    });

    els.roundCreateForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        await api("/api/admin/rounds", {
          method: "POST",
          body: JSON.stringify({
            name: els.roundCreateName.value.trim(),
            sequence: Number(els.roundCreateSequence.value),
          }),
        });
        els.roundCreateForm.reset();
        await loadRounds();
        renderRoundsTable();
      } catch (error) {
        showAlert(error.message);
      }
    });
    els.roundsTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var roundId = Number(btn.getAttribute("data-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "delete-round") {
          if (!window.confirm("Delete this round?")) return;
          await api("/api/admin/rounds/" + roundId, { method: "DELETE" });
          await loadRounds();
          renderRoundsTable();
          await loadAdminTab(state.admin.activeTab);
        }
        if (action === "edit-round") {
          var existing = null;
          for (var i = 0; i < state.rounds.length; i += 1) if (state.rounds[i].id === roundId) existing = state.rounds[i];
          var name = window.prompt("Round name:", existing ? existing.name : "");
          if (name == null) return;
          var seq = window.prompt("Round sequence:", existing ? existing.sequence : "");
          if (seq == null) return;
          await api("/api/admin/rounds/" + roundId, {
            method: "PUT",
            body: JSON.stringify({ name: name.trim(), sequence: Number(seq) }),
          });
          await loadRounds();
          renderRoundsTable();
        }
      } catch (error) {
        showAlert(error.message);
      }
    });

    els.judgeCreateForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        await api("/api/admin/judges", {
          method: "POST",
          body: JSON.stringify({ name: els.judgeCreateName.value.trim(), password: els.judgeCreatePassword.value }),
        });
        els.judgeCreateForm.reset();
        await loadJudges();
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
          var password = window.prompt("New password (optional):");
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
            ps_id: els.teamCreatePsId.value.trim(),
            category: els.teamCreateCategory.value,
            problem_statement: els.teamCreateProblem.value,
            expected_solution: els.teamCreateExpected.value,
          }),
        });
        els.teamCreateForm.reset();
        await loadTeamsAdmin();
      } catch (error) { showAlert(error.message); }
    });
    els.teamsTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var id = Number(btn.getAttribute("data-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "view-remarks") {
          var tname = btn.getAttribute("data-name");
          els.modalTeamName.textContent = "Variant Remarks: " + tname;
          els.modalRemarksList.innerHTML = "<p class='muted'>Syncing with the Spider-Verse...</p>";
          els.remarksModal.classList.remove("hidden");
          var remarks = await api("/api/admin/teams/" + id + "/remarks");
          els.modalRemarksList.innerHTML = remarks.map(function(r) {
            return "<div class='remark-item'>" +
              "<div class='remark-meta'><strong>" + escapeHtml(r.judge_name) + "</strong><span class='muted'>" + escapeHtml(r.round_name) + "</span></div>" +
              "<p class='remark-text'>" + (escapeHtml(r.remarks) || "Canon signal lost (No comment).") + "</p>" +
              "</div>";
          }).join("") || "<p class='muted' style='padding: 2rem; text-align: center;'>No multi-versal echoes found.</p>";
        }
        if (action === "view-info") {
          var team = null;
          for (var i = 0; i < state.admin.teams.length; i += 1) if (state.admin.teams[i].id === id) team = state.admin.teams[i];
          if (team) {
             els.infoTeamName.textContent = team.name;
             els.infoPsId.textContent = team.ps_id || "N/A";
             els.infoCategory.innerHTML = "<span class='cat-chip " + (team.category === "HW" ? "cat-hw" : "cat-sw") + "'>" + team.category + "</span>";
             els.infoProblem.textContent = team.problem_statement || "No description provided.";
             els.infoExpected.textContent = team.expected_solution || "No expected solution provided.";
             els.teamInfoModal.classList.remove("hidden");
          }
        }
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
          body: JSON.stringify({
            round_id: state.activeRoundId,
            name: els.criteriaCreateName.value.trim(),
            max_score: Number(els.criteriaCreateMax.value),
          }),
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
          await api("/api/admin/criteria/" + id, {
            method: "PUT",
            body: JSON.stringify({ name: name.trim(), max_score: Number(max) }),
          });
          await loadCriteria();
        }
      } catch (error) { showAlert(error.message); }
    });

    els.assignmentJudgeSelect.addEventListener("change", renderAssignmentChecks);
    els.saveAssignmentBtn.addEventListener("click", async function () {
      var judgeId = Number(els.assignmentJudgeSelect.value || 0);
      var boxes = document.querySelectorAll(".track-boxes input[type='checkbox']");
      var teamIds = [];
      for (var i = 0; i < boxes.length; i += 1) if (boxes[i].checked) teamIds.push(Number(boxes[i].value));
      try {
        await api("/api/admin/assignments/" + judgeId, {
          method: "PUT",
          body: JSON.stringify({ round_id: state.activeRoundId, team_ids: teamIds }),
        });
        showAlert("Assignments saved.", "info");
        await loadAssignments();
      } catch (error) { showAlert(error.message); }
    });

    els.refreshScoresBtn.addEventListener("click", function () {
      loadScores().catch(function (error) { showAlert(error.message); });
    });

    els.refreshRankingsBtn.addEventListener("click", function () {
      loadRankings().catch(function (error) { showAlert(error.message); });
    });
    els.rankingRoundSelect.addEventListener("change", function () {
      loadRankings().catch(function (error) { showAlert(error.message); });
    });
    els.rankingCategorySelect.addEventListener("change", function () {
      loadRankings().catch(function (error) { showAlert(error.message); });
    });

    els.rankingsTableBody.addEventListener("click", async function (event) {
      var btn = event.target.closest("button[data-action]");
      if (!btn) return;
      var row = btn.closest("tr[data-team-id]");
      if (!row) return;
      var teamId = Number(row.getAttribute("data-team-id"));
      var action = btn.getAttribute("data-action");
      try {
        if (action === "save-override") {
          var rank = Number((row.querySelector("input[data-override-rank]").value || "").trim());
          var reason = row.querySelector("input[data-override-reason]").value || "";
          if (!rank || rank < 1) return showAlert("Enter valid override rank.");
          await api("/api/admin/rankings/override", {
            method: "PUT",
            body: JSON.stringify({ round_id: state.activeRoundId, team_id: teamId, override_rank: rank, reason: reason }),
          });
          await loadRankings();
        }
        if (action === "clear-override") {
          await api("/api/admin/rankings/override/" + teamId + roundQuery(), { method: "DELETE" });
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
      await loadRounds();
      var pillSpan = els.userNamePill.querySelector("span");
      if (pillSpan) pillSpan.textContent = state.user.name + " (" + state.user.role + ")";
      else els.userNamePill.textContent = state.user.name + " (" + state.user.role + ")";
      if (state.user.role === "admin") {

        showMode("admin");
        switchAdminTab("overview");
        startAdminAutoRefresh();
      } else if (state.user.role === "judge") {
        showMode("judge");
        await loadJudgeTeams();
      } else {
        showMode("login");
      }
    } catch (_error) {
      showMode("login");
    }
  }

  bootstrap();
})();
