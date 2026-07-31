import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 기본값은 localhost 만 바인딩해서 같은 네트워크의 다른 기기에서 접근이 안 됩니다.
    // true = 0.0.0.0 바인딩 → http://<내부IP>:5173 으로 접속 가능
    host: true,
    port: 5173,
    // 백엔드를 붙이면 VITE_API=http 로 두고 이 프록시를 통해 호출합니다.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
