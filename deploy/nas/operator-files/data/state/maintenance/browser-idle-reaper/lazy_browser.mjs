import { randomUUID } from 'node:crypto';

const PREFIX = 'openclaw-lazy-sandbox:';
const pending = new Map();
const cleanup = new FinalizationRegistry(key => pending.delete(key));

// Contexts and tool executions hold strong references; the index does not retain
// abandoned sessions or their workspace authority indefinitely.
export function deferredSandboxBrowser(provision) {
  const bridgeUrl = PREFIX + randomUUID();
  let inFlight;
  const browser = {
    bridgeUrl,
    async ensure() {
      if (!inFlight) {
        inFlight = Promise.resolve().then(provision).finally(() => { inFlight = undefined; });
      }
      return await inFlight;
    }
  };
  pending.set(bridgeUrl, new WeakRef(browser));
  cleanup.register(browser, bridgeUrl);
  return browser;
}

export function wrapDeferredBrowserTool(opts, factory, track, routedParams = value => value) {
  const url = opts?.sandboxBridgeUrl;
  const deferred = typeof url === 'string' && url.startsWith(PREFIX) ? pending.get(url)?.deref() : undefined;
  if (typeof url === 'string' && url.startsWith(PREFIX) && !deferred) {
    throw new Error('Deferred sandbox browser authority is unavailable');
  }
  const catalogTool = factory(opts);
  return {
    ...catalogTool,
    async execute(...args) {
      args[2]?.throwIfAborted();
      const params = routedParams(args[1]);
      const requestedNode = typeof params?.node === 'string' ? params.node.trim() : params?.node;
      const sandboxTarget = Boolean(url) && !requestedNode &&
        (params?.target == null || params.target === 'sandbox');
      if (!sandboxTarget) return await catalogTool.execute(...args);
      if (!deferred) return await track(opts, catalogTool.execute, ...args);
      const runtime = await deferred.ensure();
      args[2]?.throwIfAborted();
      if (!runtime?.bridgeUrl || runtime.bridgeUrl.startsWith(PREFIX)) {
        throw new Error('Sandbox browser provisioning did not return a real bridge');
      }
      const liveOpts = { ...opts, sandboxBridgeUrl: runtime.bridgeUrl };
      const liveTool = factory(liveOpts);
      return await track(liveOpts, liveTool.execute, ...args);
    }
  };
}
