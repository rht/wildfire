// Apply the website's rules to the isolated diagram entry without changing app tooling.
const base = require("../eslint.config.cjs");
module.exports = base.map((entry) =>
  entry.files?.includes("src/**/*.{js,jsx,mjs}")
    ? { ...entry, files: ["pipeline/**/*.{jsx,mjs}"] }
    : entry,
);
