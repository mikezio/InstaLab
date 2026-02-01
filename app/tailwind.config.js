/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./django_app/dashboard/templates/**/*.html",
    "./django_app/static/**/*.js",
    "./ui_src/**/*.{html,js,css}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#121418",
        muted: "#6b7280",
        paper: "#fbf7f1",
        sand: "#f3ede4",
        mist: "#efe7dc",
        accent: "#1f4fff",
        accent2: "#f5a524",
        accent3: "#10b981",
        danger: "#ef4444",
        slate: "#2c3140"
      },
      fontFamily: {
        display: ["Space Grotesk", "system-ui", "sans-serif"],
        body: ["Space Grotesk", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"]
      },
      boxShadow: {
        soft: "0 12px 30px rgba(17, 24, 39, 0.12)",
        float: "0 20px 50px rgba(17, 24, 39, 0.16)",
        inset: "inset 0 0 0 1px rgba(17, 24, 39, 0.08)"
      },
      borderRadius: {
        xl: "22px",
        lg: "18px"
      }
    }
  },
  plugins: []
};
