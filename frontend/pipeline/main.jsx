import React from "react";
import { createRoot } from "react-dom/client";
import { Button, CssBaseline, ThemeProvider } from "@mui/material";
import DownloadOutlined from "@ant-design/icons/DownloadOutlined";
import EnvironmentOutlined from "@ant-design/icons-svg/es/asn/EnvironmentOutlined";
import NodeIndexOutlined from "@ant-design/icons-svg/es/asn/NodeIndexOutlined";
import DashboardOutlined from "@ant-design/icons-svg/es/asn/DashboardOutlined";
import { theme } from "../src/theme";
import "@fontsource/public-sans/400.css";
import "@fontsource/public-sans/600.css";
import "@fontsource/public-sans/700.css";
import "@fontsource/public-sans/800.css";
import "./pipeline.css";

const colors = theme.palette;
const steps = [
  {
    title: "Assess risk",
    icon: EnvironmentOutlined,
    lines: ["Find nearby buildings and people.", "Estimate risk and value."],
  },
  {
    title: "Coordinate response",
    icon: NodeIndexOutlined,
    lines: ["Prioritise calls.", "Plan crew interventions."],
  },
  {
    title: "Guide the analyst",
    icon: DashboardOutlined,
    lines: ["See routes, assistance needs", "and next actions."],
  },
];

// Render the website's Ant Design icon paths directly as vector SVG.
function iconNode(node, key) {
  return React.createElement(
    node.tag,
    { ...node.attrs, key },
    node.children?.map(iconNode),
  );
}

function PitchSlide() {
  return (
    <svg
      className="pitch-slide"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 1600 900"
      role="img"
      aria-labelledby="slide-title"
      aria-describedby="slide-description"
    >
      <title id="slide-title">From fire alert to coordinated response</title>
      <desc id="slide-description">
        Assess risk: find nearby buildings and people, estimate risk and value.
        Coordinate response: prioritise calls and plan crew interventions. Guide
        the analyst: see routes, assistance needs and next actions. Live updates
        refine priorities.
      </desc>
      <defs>
        <linearGradient id="brand-orange">
          <stop stopColor="#e75632" />
          <stop offset="1" stopColor="#ffb02e" />
        </linearGradient>
        <marker
          id="arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto"
        >
          <path
            d="M1 1 9 5 1 9"
            fill="none"
            stroke={colors.primary.main}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </marker>
      </defs>
      <rect width="1600" height="900" fill={colors.background.paper} />
      <g fontFamily="Public Sans, sans-serif" fill={colors.text.primary}>
        <text x="100" y="88" fontSize="31" fontWeight="800" letterSpacing="-1">
          Respons<tspan fill="url(#brand-orange)">Ara</tspan>
        </text>
        <text
          x="1500"
          y="84"
          textAnchor="end"
          fontSize="19"
          fontWeight="600"
          fill={colors.text.secondary}
        >
          Nobody left behind
        </text>
        <line x1="100" y1="121" x2="1500" y2="121" stroke={colors.divider} />
        <text
          x="100"
          y="229"
          fontSize="52"
          fontWeight="700"
          letterSpacing="-1.6"
        >
          From fire alert to coordinated response
        </text>
        <text x="100" y="313" fontSize="21" fill={colors.text.secondary}>
          Fire location + spread forecast
        </text>
        <path
          d="M260 330 V365"
          fill="none"
          stroke={colors.primary.main}
          strokeWidth="2"
          markerEnd="url(#arrow)"
        />
        {steps.map((step, index) => (
          <g key={step.title} transform={`translate(${100 + index * 480} 386)`}>
            <rect
              width="440"
              height="284"
              rx={theme.shape.borderRadius}
              fill={colors.background.paper}
              stroke={colors.divider}
              strokeWidth="1.5"
            />
            <svg
              x="30"
              y="29"
              width="43"
              height="43"
              viewBox="0 0 1024 1024"
              fill={colors.primary.main}
              aria-hidden="true"
            >
              {step.icon.icon.children.map(iconNode)}
            </svg>
            <text
              x="404"
              y="59"
              textAnchor="end"
              fontSize="19"
              fill={colors.text.secondary}
            >
              0{index + 1}
            </text>
            <text
              x="30"
              y="124"
              fontSize="32"
              fontWeight="700"
              letterSpacing="-.7"
            >
              {step.title}
            </text>
            {step.lines.map((line, i) => (
              <text
                key={line}
                x="30"
                y={178 + i * 37}
                fontSize="23"
                fill={colors.text.secondary}
              >
                {line}
              </text>
            ))}
          </g>
        ))}
        <g
          fill="none"
          stroke={colors.primary.main}
          strokeWidth="2"
          markerEnd="url(#arrow)"
        >
          <path d="M548 528 H571" />
          <path d="M1028 528 H1051" />
          <path d="M1280 693 V756 Q1280 770 1266 770 H334 Q320 770 320 756 V694" />
        </g>
        <rect
          x="582"
          y="749"
          width="436"
          height="42"
          fill={colors.background.paper}
        />
        <text
          x="800"
          y="778"
          textAnchor="middle"
          fontSize="23"
          fontWeight="600"
          fill={colors.primary.main}
        >
          Live updates refine priorities
        </text>
      </g>
    </svg>
  );
}

function Pipeline() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <main className="pitch-page">
        <nav className="export-controls" aria-label="Slide exports">
          <Button
            component="a"
            href="./exports/responsara-pipeline.png"
            download
            startIcon={<DownloadOutlined aria-hidden="true" />}
          >
            PNG image
          </Button>
          <Button
            component="a"
            href="./exports/responsara-pipeline.pdf"
            download
            variant="outlined"
            startIcon={<DownloadOutlined aria-hidden="true" />}
          >
            Vector PDF
          </Button>
        </nav>
        <PitchSlide />
      </main>
    </ThemeProvider>
  );
}

createRoot(document.getElementById("root")).render(<Pipeline />);
