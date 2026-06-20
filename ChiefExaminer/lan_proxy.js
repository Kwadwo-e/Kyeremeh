const http = require("http");

const listenHost = process.env.LAN_PROXY_HOST || "0.0.0.0";
const listenPort = Number(process.env.LAN_PROXY_PORT || 8000);
const targetHost = process.env.APP_HOST || "127.0.0.1";
const targetPort = Number(process.env.APP_PORT || 8002);

const server = http.createServer((clientReq, clientRes) => {
  const headers = {
    ...clientReq.headers,
    host: `${targetHost}:${targetPort}`,
    "x-forwarded-host": clientReq.headers.host || `${listenHost}:${listenPort}`,
    "x-forwarded-port": String(listenPort),
    "x-forwarded-proto": "http",
    "x-forwarded-for": [
      clientReq.socket.remoteAddress,
      clientReq.headers["x-forwarded-for"],
    ].filter(Boolean).join(", "),
    "x-lan-proxy": "1",
  };
  const proxyReq = http.request(
    {
      hostname: targetHost,
      port: targetPort,
      path: clientReq.url,
      method: clientReq.method,
      headers,
    },
    (proxyRes) => {
      clientRes.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      proxyRes.pipe(clientRes);
    }
  );

  proxyReq.on("error", () => {
    clientRes.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    clientRes.end("ChiefExam backend is not reachable.");
  });

  clientReq.pipe(proxyReq);
});

server.listen(listenPort, listenHost, () => {
  console.log(
    `ChiefExam LAN proxy running at http://${listenHost}:${listenPort} -> http://${targetHost}:${targetPort}`
  );
});
