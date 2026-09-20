import { createTheme } from "@mui/material/styles";
import { themes } from "../src/theme";

// ResponsAra pitch-film palette (session 155), scoped to this diagram.
export const film = {
  bg: "#15100D",
  bg2: "#1C1613",
  panel: "#1D1714",
  line: "#2E2622",
  ink: "#EFE9E2",
  dim: "#C9C0B7",
  muted: "#9A928A",
  red: "#C33A2C",
  redText: "#E4705C",
  orange: "#CC5B22",
  amber: "#D08A12",
};

export const theme = createTheme(themes.dark, {
  palette: {
    primary: { main: film.redText, contrastText: film.bg },
    background: { default: film.bg, paper: film.panel },
    text: { primary: film.ink, secondary: film.dim },
    divider: film.line,
  },
  shape: { borderRadius: 14 },
});
