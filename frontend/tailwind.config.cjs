/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./frontend/index.html",
    "./frontend/src/**/*.{ts,tsx}",
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#1f2937",
        line: "#eef1f8",
        powder: "#f5f7fc",
        bluewash: "#ecf3ff",
      },
      boxShadow: {
        soft: "0 4px 18px rgba(83, 108, 156, 0.06)",
        chip: "0 6px 18px rgba(121, 142, 174, 0.08)",
      },
      borderRadius: {
        "4xl": "2rem",
      },
      fontFamily: {
        sans: ["PingFang SC", "Microsoft YaHei", "Noto Sans SC", "sans-serif"],
      },
    },
  },
  plugins: [],
};
