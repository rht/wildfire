module.exports = [
  { ignores: ["dist/**", "node_modules/**", "artifacts/**"] },
  {
    files: ["src/**/*.{js,jsx,mjs}", "vendor/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        window: "readonly",
        document: "readonly",
        location: "readonly",
        fetch: "readonly",
        AbortSignal: "readonly",
        ResizeObserver: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unreachable": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-eval": "error",
    },
  },
  {
    files: ["legacy/dashboard-*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        document: "readonly",
        L: "readonly",
        module: "readonly",
        location: "readonly",
        fetch: "readonly",
        AbortSignal: "readonly",
      },
    },
    rules: {
      "no-unused-vars": "error",
      "no-undef": "error",
      "no-unreachable": "error",
      eqeqeq: ["error", "always"],
      "no-eval": "error",
    },
  },
];
