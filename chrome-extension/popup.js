const elements = {
  signedOut: document.querySelector('#signed-out'),
  controls: document.querySelector('#controls'),
  jobSelect: document.querySelector('#job-select'),
  refresh: document.querySelector('#refresh'),
  summary: document.querySelector('#summary'),
  candidateCount: document.querySelector('#candidate-count'),
  batchCount: document.querySelector('#batch-count'),
  status: document.querySelector('#status'),
  progress: document.querySelector('#progress'),
  inspect: document.querySelector('#inspect'),
  export: document.querySelector('#export'),
  confirmation: document.querySelector('#confirmation'),
  confirmCopy: document.querySelector('#confirm-copy'),
  cancel: document.querySelector('#cancel'),
  confirm: document.querySelector('#confirm'),
  openGreenhouse: document.querySelector('#open-greenhouse'),
  retryConnection: document.querySelector('#retry-connection'),
  connectionError: document.querySelector('#connection-error'),
};

let selection = null;

async function message(type, payload = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || (tab.url && !tab.url.startsWith('https://app.greenhouse.io/'))) {
    throw new Error('Open an authenticated Greenhouse tab before using the extension.');
  }
  // Run inside Greenhouse's MAIN JavaScript world. This uses the exact same
  // origin, authenticated cookies, and CSRF context as the open page.
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    files: ['page-operations.js'],
  });
  const [execution] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (operation, operationPayload) => {
      try {
        const result = await window.__greenhouseResumeExporter.run(operation, operationPayload);
        return { ok: true, ...result };
      } catch (error) {
        return { ok: false, error: error?.message || String(error) };
      }
    },
    args: [type, payload],
  });
  if (!execution?.result) throw new Error('Greenhouse did not return an extension result.');
  return execution.result;
}

function setBusy(busy) {
  elements.jobSelect.disabled = busy;
  elements.refresh.disabled = busy;
  elements.inspect.disabled = busy || !elements.jobSelect.value;
  elements.export.disabled = busy || !selection;
}

function showSignedOut(errorMessage) {
  elements.connectionError.textContent = errorMessage || 'Sign in to Greenhouse in this Chrome profile, then try again.';
  elements.signedOut.classList.remove('hidden');
  elements.controls.classList.add('hidden');
}

function showControls() {
  elements.signedOut.classList.add('hidden');
  elements.controls.classList.remove('hidden');
}

async function loadJobs() {
  showControls();
  setBusy(true);
  elements.status.textContent = 'Loading accessible jobs…';
  elements.summary.classList.add('hidden');
  selection = null;
  try {
    const response = await message('LIST_JOBS');
    if (!response.ok) throw new Error(response.error);
    elements.jobSelect.replaceChildren();
    const placeholder = new Option('Choose a vacancy…', '');
    elements.jobSelect.add(placeholder);
    for (const job of response.jobs) {
      const option = new Option(`${job.name} (${job.id})`, job.id);
      option.dataset.name = job.name;
      elements.jobSelect.add(option);
    }
    elements.status.textContent = `${response.jobs.length} accessible ${response.jobs.length === 1 ? 'job' : 'jobs'} found.`;
    setBusy(false);
  } catch (error) {
    if (/sign in|login|authenticated|Greenhouse tab|Cannot access|executeScript|permission/i.test(error.message)) {
      showSignedOut(`Connection error: ${error.message}`);
    }
    else elements.status.textContent = `Could not load jobs: ${error.message}`;
  }
}

async function inspectJob() {
  const jobId = elements.jobSelect.value;
  if (!jobId) return;
  setBusy(true);
  elements.status.textContent = 'Counting candidates across job pages…';
  try {
    const response = await message('LIST_APPLICATIONS', { jobId });
    if (!response.ok) throw new Error(response.error);
    selection = { jobId, applications: response.applications };
    elements.candidateCount.textContent = response.applications.length;
    elements.batchCount.textContent = Math.ceil(response.applications.length / 30);
    elements.summary.classList.remove('hidden');
    elements.status.textContent = 'Ready to export. Review the counts before continuing.';
  } catch (error) {
    selection = null;
    elements.status.textContent = `Could not count candidates: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

elements.jobSelect.addEventListener('change', () => {
  selection = null;
  elements.summary.classList.add('hidden');
  elements.inspect.disabled = !elements.jobSelect.value;
  elements.export.disabled = true;
  elements.status.textContent = elements.jobSelect.value ? 'Count candidates before exporting.' : 'Choose a vacancy.';
});
elements.inspect.addEventListener('click', inspectJob);
elements.refresh.addEventListener('click', loadJobs);
elements.openGreenhouse.addEventListener('click', () => chrome.tabs.create({ url: 'https://app.greenhouse.io/alljobs' }));
elements.retryConnection.addEventListener('click', loadJobs);
elements.export.addEventListener('click', () => {
  const count = selection.applications.length;
  const batches = Math.ceil(count / 30);
  elements.confirmCopy.textContent = `Submit ${count} applications in ${batches} Greenhouse email ${batches === 1 ? 'batch' : 'batches'}?`;
  elements.confirmation.classList.remove('hidden');
});
elements.cancel.addEventListener('click', () => elements.confirmation.classList.add('hidden'));
elements.confirm.addEventListener('click', async () => {
  elements.confirmation.classList.add('hidden');
  setBusy(true);
  elements.progress.max = Math.ceil(selection.applications.length / 30);
  elements.progress.value = 0;
  elements.progress.classList.remove('hidden');
  elements.status.textContent = 'Submitting export batches…';
  try {
    const response = await message('EXPORT_RESUMES', selection);
    if (!response.ok) throw new Error(response.error);
    elements.progress.value = response.submittedBatches;
    elements.status.textContent = `${response.submittedBatches} batches submitted. Greenhouse will email the PDFs.`;
  } catch (error) {
    elements.status.textContent = `Export stopped: ${error.message}`;
  } finally {
    setBusy(false);
  }
});

loadJobs();
