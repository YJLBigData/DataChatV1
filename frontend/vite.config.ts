import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: resolve(__dirname),
  base: "/web/",
  plugins: [react()],
  publicDir: false,
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8001",
      "/health": "http://127.0.0.1:8001",
    },
  },
  build: {
    outDir: resolve(__dirname, "../backend/web"),
    emptyOutDir: true,
    target: ["chrome107", "edge107", "safari15"],
    cssTarget: "safari15",
    // chart-vendor(ECharts) 已通过 React.lazy + 独立 manualChunk 拆分，
    // 仅当答案含图表时按需加载，不进入首屏/登录路径。把告警阈值设为 1200KB，
    // 避免“已懒加载的大块”刷屏从而掩盖真正异常的大包。
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      input: resolve(__dirname, "index.html"),
      output: {
        entryFileNames: "static/smartq/[name]-[hash].js",
        chunkFileNames: "static/smartq/[name]-[hash].js",
        assetFileNames: "static/smartq/[name]-[hash][extname]",
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("/echarts/") || id.includes("/zrender/")) {
            return "chart-vendor";
          }
          if (id.includes("/framer-motion/") || id.includes("/motion-")) {
            return "motion-vendor";
          }
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) {
            return "react-vendor";
          }
          return "vendor";
        },
      },
    },
  },
});
