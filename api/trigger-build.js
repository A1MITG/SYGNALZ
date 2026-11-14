// Starts "Build Signals (static)" on GitHub. Vercel Cron calls this at 09:00
// and 19:00 IST (vercel.json, written by .github/workflows/build-signals.yml)
// because GitHub's own scheduler starts that workflow 4-7 hours late, while a
// run started through the API begins within seconds. BP-56.
//
// Needs two environment variables in the Vercel project (Production):
//   CRON_SECRET             any long random string. Vercel sends it as
//                           "Authorization: Bearer <CRON_SECRET>" with every
//                           cron call; any other caller is refused.
//   GITHUB_DISPATCH_TOKEN   a fine-grained GitHub token for A1MITG/SYGNALZ
//                           only, with "Actions: Read and write" only.
// Without either, it does nothing (fails closed).

const REPO = 'A1MITG/SYGNALZ';
const WORKFLOW = 'build-signals.yml';
const BRANCH = 'final';

function github(path, token, init = {}) {
    return fetch(`https://api.github.com/repos/${REPO}${path}`, {
        ...init,
        headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'signal-build-trigger',
            ...(init.headers || {}),
        },
    });
}

module.exports = async function handler(req, res) {
    const secret = process.env.CRON_SECRET;
    const token = process.env.GITHUB_DISPATCH_TOKEN;
    if (!secret || !token) {
        return res.status(503).json({ started: false, error: 'CRON_SECRET or GITHUB_DISPATCH_TOKEN is not set' });
    }
    if (req.headers.authorization !== `Bearer ${secret}`) {
        return res.status(401).json({ started: false, error: 'unauthorized' });
    }

    // One build at a time: a late GitHub-scheduled run may already be going.
    const runs = await github(`/actions/workflows/${WORKFLOW}/runs?per_page=5`, token);
    if (runs.ok) {
        const busy = (await runs.json()).workflow_runs.some(run => run.status !== 'completed');
        if (busy) {
            return res.status(200).json({ started: false, reason: 'a build is already queued or running' });
        }
    }

    const dispatch = await github(`/actions/workflows/${WORKFLOW}/dispatches`, token, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref: BRANCH }),
    });
    if (dispatch.status === 204) {
        return res.status(200).json({ started: true });
    }
    return res.status(502).json({ started: false, error: `GitHub answered ${dispatch.status}` });
};
