(() => {
  if (window.__greenhouseResumeExporter) return;

  const BATCH_SIZE = 30;

  async function greenhouseFetch(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      redirect: 'follow',
      ...options,
    });
    const loginPage = response.url.includes('/users/sign_in')
      || response.url.includes('/login')
      || response.url.includes('accounts.google.com');
    if (loginPage) throw new Error(`Greenhouse session is not authenticated (redirected to ${response.url}).`);
    return response;
  }

  function parseDocument(html) {
    return new DOMParser().parseFromString(html, 'text/html');
  }

  async function listJobs() {
    // `/alljobs` is partly client-rendered. Prefer the already-rendered live
    // page; a plain fetch can contain no job anchors even when authenticated.
    let parsed = document;
    if (location.pathname !== '/alljobs') {
      const response = await greenhouseFetch('/alljobs');
      if (!response.ok) throw new Error(`Greenhouse /alljobs returned HTTP ${response.status} at ${response.url}.`);
      parsed = parseDocument(await response.text());
    }
    const jobs = new Map();
    for (const anchor of parsed.querySelectorAll('a[href*="/sdash/"]')) {
      const match = anchor.href.match(/\/sdash\/(\d+)/);
      if (!match) continue;
      const id = match[1];
      const name = anchor.textContent.replace(/\s+/g, ' ').trim() || `Job ${id}`;
      if (!jobs.has(id) || name.length > jobs.get(id).name.length) jobs.set(id, { id, name });
    }
    if (!jobs.size) {
      throw new Error(`No rendered /sdash job links found. Open https://app.greenhouse.io/alljobs, wait for the jobs to appear, then click Retry. Page: ${location.href}; title: ${parsed.title || '(none)'}`);
    }
    return { jobs: [...jobs.values()].sort((a, b) => a.name.localeCompare(b.name)) };
  }

  function candidatePath(jobId, page) {
    const query = new URLSearchParams({
      hiring_plan_id: jobId,
      job_status: 'open',
      sort: 'last_activity desc',
      stage_status_id: '2',
      type: 'all',
      page: String(page),
    });
    return `/plans/${jobId}/candidates?${query}`;
  }

  async function listApplications(jobId) {
    if (!/^\d+$/.test(jobId || '')) throw new Error('Invalid Greenhouse job ID.');
    const applications = new Map();
    for (let page = 1; page <= 100; page += 1) {
      const response = await greenhouseFetch(candidatePath(jobId, page));
      if (!response.ok) throw new Error(`Candidate page ${page} returned HTTP ${response.status}.`);
      const html = await response.text();
      const matches = [...html.matchAll(/\/people\/(\d+)\/applications\/(\d+)(?:\/redesign)?/g)];
      if (!matches.length) break;
      for (const match of matches) applications.set(match[2], { personId: match[1], applicationId: match[2] });
    }
    return { applications: [...applications.values()] };
  }

  async function csrfToken() {
    const existing = document.querySelector('meta[name="csrf-token"]')?.content;
    if (existing) return existing;
    const response = await greenhouseFetch('/alljobs');
    const parsed = parseDocument(await response.text());
    const token = parsed.querySelector('meta[name="csrf-token"]')?.content;
    if (!token) throw new Error('Greenhouse CSRF token was not found.');
    return token;
  }

  async function exportResumes(applications) {
    if (!Array.isArray(applications) || !applications.length) throw new Error('No applications selected.');
    const token = await csrfToken();
    const batches = [];
    for (let index = 0; index < applications.length; index += BATCH_SIZE) {
      batches.push(applications.slice(index, index + BATCH_SIZE));
    }
    for (let index = 0; index < batches.length; index += 1) {
      const body = new URLSearchParams({ sort: '' });
      for (const application of batches[index]) body.append('application_ids[]', application.applicationId);
      const response = await greenhouseFetch('/people/bulk/print_resumes', {
        method: 'POST',
        headers: {
          Accept: 'application/json, text/javascript, */*; q=0.01',
          'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
          'X-CSRF-Token': token,
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: body.toString(),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || result?.status !== 'success') {
        throw new Error(result?.message || `Batch ${index + 1} returned HTTP ${response.status}.`);
      }
    }
    return { submittedBatches: batches.length };
  }

  window.__greenhouseResumeExporter = {
    run(operation, payload = {}) {
      const operations = {
        LIST_JOBS: () => listJobs(),
        LIST_APPLICATIONS: () => listApplications(payload.jobId),
        EXPORT_RESUMES: () => exportResumes(payload.applications),
      };
      if (!operations[operation]) throw new Error(`Unsupported operation: ${operation}`);
      return operations[operation]();
    },
  };
})();
