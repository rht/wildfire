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
    title: "Assess risk & value",
    icon: EnvironmentOutlined,
    x: 80,
    width: 410,
    blocks: [
      {
        y: 135,
        lines: ["Discover nearby buildings,", "infrastructure and people."],
      },
      {
        y: 214,
        lines: [
          "LLM estimates monetary value,",
          "importance and vulnerability.",
        ],
      },
      {
        y: 293,
        lines: [
          "Sourced exposure, mobility",
          "and evacuation time",
          "inform urgency.",
        ],
      },
      { y: 413, lines: ["Unknowns stay visible."], accent: true },
    ],
  },
  {
    title: "Prioritise action",
    icon: NodeIndexOutlined,
    x: 530,
    width: 580,
    blocks: [
      { y: 126, lines: ["Calls"], label: true },
      {
        y: 159,
        lines: [
          "Least time left first:",
          "time until fire − evacuation time",
          "− safety buffer",
        ],
      },
      { y: 274, lines: ["Crews"], label: true },
      {
        y: 307,
        lines: [
          "Compare short sequences of stops.",
          "Help first: people needing assistance,",
          "then total people, then operational value.",
          "Use safe routes, travel/help time & capacity.",
        ],
      },
      { y: 437, lines: ["Late or unsafe → urgent review."], accent: true },
    ],
  },
  {
    title: "Analyst’s plan",
    icon: DashboardOutlined,
    x: 1150,
    width: 370,
    blocks: [
      {
        y: 135,
        lines: [
          "Where crews go.",
          "Where people evacuate.",
          "Who needs follow-up.",
        ],
      },
      { y: 295, lines: ["Analyst reviews", "and approves."], label: true },
    ],
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
        Discover nearby buildings, infrastructure and people. LLM estimates
        monetary value and vulnerability; sourced exposure, mobility and
        evacuation time inform urgency. Unknowns stay visible. Calls prioritise
        the least time left: time until fire minus evacuation time minus safety
        buffer. Crews compare short sequences, prioritising assisted people,
        then total people, then operational value, accounting for travel/help
        time, safe routes and capacity. Late or unsafe actions need urgent
        review. The analyst reviews crew routes, evacuation destinations and
        follow-up, then approves. Calls and changing fire conditions update the
        plan.
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
        <text x="80" y="72" fontSize="31" fontWeight="800" letterSpacing="-1">
          Respons<tspan fill="url(#brand-orange)">Ara</tspan>
        </text>
        <text
          x="1520"
          y="68"
          textAnchor="end"
          fontSize="19"
          fontWeight="600"
          fill={colors.text.secondary}
        >
          Nobody left behind
        </text>
        <line x1="80" y1="101" x2="1520" y2="101" stroke={colors.divider} />
        <text
          x="80"
          y="190"
          fontSize="48"
          fontWeight="700"
          letterSpacing="-1.6"
        >
          From fire alert to coordinated response
        </text>
        <text x="80" y="260" fontSize="21" fill={colors.text.secondary}>
          Fire location + spread forecast
        </text>
        <path
          d="M260 276 V307"
          fill="none"
          stroke={colors.primary.main}
          strokeWidth="2"
          markerEnd="url(#arrow)"
        />
        {steps.map((step, index) => (
          <g
            key={step.title}
            transform={`translate(${step.x} 325)`}
            data-card={step.title}
          >
            <rect
              className="card-frame"
              width={step.width}
              height="460"
              rx={theme.shape.borderRadius}
              fill={colors.background.paper}
              stroke={colors.divider}
              strokeWidth="1.5"
            />
            <svg
              x="28"
              y="26"
              width="32"
              height="32"
              viewBox="0 0 1024 1024"
              fill={colors.primary.main}
              aria-hidden="true"
            >
              {step.icon.icon.children.map(iconNode)}
            </svg>
            <text
              x={step.width - 28}
              y="51"
              textAnchor="end"
              fontSize="17"
              fill={colors.text.secondary}
            >
              0{index + 1}
            </text>
            <text
              x="28"
              y="91"
              fontSize="30"
              fontWeight="700"
              letterSpacing="-.7"
            >
              {step.title}
            </text>
            {index === 1 && (
              <line
                x1="28"
                y1="240"
                x2={step.width - 28}
                y2="240"
                stroke={colors.divider}
              />
            )}
            {step.blocks.map((block) =>
              block.lines.map((line, i) => (
                <text
                  key={line}
                  x="28"
                  y={block.y + i * 31}
                  fontSize={block.accent ? 20 : 22}
                  fontWeight={block.label || block.accent ? 600 : 400}
                  fill={
                    block.label || block.accent
                      ? colors.primary.main
                      : colors.text.secondary
                  }
                >
                  {line}
                </text>
              )),
            )}
          </g>
        ))}
        <g
          fill="none"
          stroke={colors.primary.main}
          strokeWidth="2"
          markerEnd="url(#arrow)"
        >
          <path d="M498 548 H521" />
          <path d="M1118 548 H1141" />
          <path d="M1335 803 V839 Q1335 849 1325 849 H295 Q285 849 285 839 V803" />
        </g>
        <rect
          x="492"
          y="828"
          width="616"
          height="42"
          fill={colors.background.paper}
        />
        <text
          x="800"
          y="856"
          textAnchor="middle"
          fontSize="21"
          fontWeight="600"
          fill={colors.primary.main}
        >
          Calls + changing fire conditions update the plan
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
