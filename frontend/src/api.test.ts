import { it, expect, vi, afterEach } from "vitest";
import { api, setCsrf } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("envoie les cookies et le jeton CSRF uniquement aux mutations", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetcher);
  setCsrf("test-token");
  await api("/jobs", {
    method: "POST",
    body: JSON.stringify({ graph: { nodes: [] } }),
  });
  expect(fetcher.mock.calls[0][0]).toBe("/api/jobs");
  expect(fetcher.mock.calls[0][1].credentials).toBe("include");
  expect(fetcher.mock.calls[0][1].headers["X-CSRF-Token"]).toBe("test-token");
  fetcher.mockResolvedValueOnce(new Response('{}',{status:200}));
  await api('/plugins');
  expect(fetcher.mock.calls[1][1].headers['X-CSRF-Token']).toBeUndefined();
});
it('explique une erreur sans corps au lieu d’afficher null',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(null,{status:500})));await expect(api('/auth/me')).rejects.toThrow('Aucun détail fourni par le serveur');});
it("ne transforme pas une erreur de validation en succès et conserve ses détails", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ loc: ["body", "graph"], msg: "invalide" }],
          }),
          { status: 422 },
        ),
      ),
  );
  await expect(api("/jobs", { method: "POST" })).rejects.toThrow("invalide");
});
it("signale les réponses hors JSON sans masquer le statut", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response("upstream indisponible", { status: 503 }),
      ),
  );
  await expect(api("/plugins")).rejects.toThrow("503");
});
