
/*
 * R.I.B.B.O.N. — Business Intelligence 2.0
 * Dashboard independente do JavaScript principal.
 * Gráficos interativos com Chart.js.
 */

(() => {
  "use strict";

  const root = document.getElementById("view-dashboard");
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const charts = new Map();

  const COLORS = {
    blue: "#0f62fe",
    cyan: "#00a6d6",
    teal: "#009d9a",
    green: "#24a148",
    purple: "#8a3ffc",
    orange: "#ff832b",
    red: "#da1e28",
    gray: "#8d8d8d",
    grid: "#e8edf3",
    text: "#525252"
  };

  const palette = [
    COLORS.blue,
    COLORS.teal,
    COLORS.purple,
    COLORS.orange,
    COLORS.cyan,
    COLORS.green,
    COLORS.red,
    COLORS.gray
  ];

  const number = (value) =>
    Number(value || 0).toLocaleString("pt-BR");

  const decimal = (value, digits = 1) =>
    value == null
      ? "—"
      : Number(value).toLocaleString("pt-BR", {
          minimumFractionDigits: 0,
          maximumFractionDigits: digits
        });

  const percent = (value) =>
    value == null ? "—" : `${decimal(value)}%`;

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[char]);

  // =====================================================
  // Estilos
  // =====================================================

  const style = document.createElement("style");
  style.textContent = `
    #ribbon-bi {
      margin: 26px 0 36px;
      color: #262626;
    }

    #ribbon-bi * {
      box-sizing: border-box;
    }

    #ribbon-bi .bi-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 20px;
    }

    #ribbon-bi .bi-toolbar h2 {
      margin: 0 0 6px;
      font-size: 23px;
      font-weight: 650;
      letter-spacing: -.5px;
    }

    #ribbon-bi .bi-toolbar p {
      margin: 0;
      font-size: 13px;
      color: #777;
    }

    #ribbon-bi .bi-filters {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    #ribbon-bi select,
    #ribbon-bi .bi-refresh {
      padding: 10px 12px;
      font: inherit;
      font-size: 13px;
      border: 1px solid #d0d4dc;
      border-radius: 6px;
      background: #fff;
      color: #262626;
      cursor: pointer;
    }

    #ribbon-bi .bi-refresh {
      background: #0f62fe;
      color: #fff;
      border-color: #0f62fe;
      font-weight: 600;
    }

    #ribbon-bi .bi-refresh:hover {
      background: #0043ce;
    }

    #ribbon-bi .bi-kpis {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 13px;
      margin-bottom: 18px;
    }

    #ribbon-bi .bi-kpi {
      background: #fff;
      border: 1px solid #e3e7ed;
      border-radius: 9px;
      padding: 19px;
      min-width: 0;
      transition: transform .2s, box-shadow .2s;
    }

    #ribbon-bi .bi-kpi:hover {
      transform: translateY(-2px);
      box-shadow: 0 7px 22px rgba(0,0,0,.06);
    }

    #ribbon-bi .bi-kpi-label {
      display: block;
      color: #626a76;
      font-size: 12px;
      line-height: 1.4;
    }

    #ribbon-bi .bi-kpi-value {
      display: block;
      margin: 12px 0 7px;
      font-size: clamp(22px, 2vw, 32px);
      font-weight: 650;
      letter-spacing: -.8px;
      font-variant-numeric: tabular-nums;
    }

    #ribbon-bi .bi-kpi-note {
      color: #838995;
      font-size: 11px;
    }

    #ribbon-bi .bi-kpi-accent {
      width: 27px;
      height: 3px;
      border-radius: 3px;
      margin-bottom: 12px;
    }

    #ribbon-bi .bi-charts {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }

    #ribbon-bi .bi-chart-card {
      background: #fff;
      border: 1px solid #e3e7ed;
      border-radius: 9px;
      padding: 22px;
      min-width: 0;
    }

    #ribbon-bi .bi-chart-wide {
      grid-column: 1 / -1;
    }

    #ribbon-bi .bi-chart-title {
      margin: 0;
      font-size: 15px;
      font-weight: 650;
    }

    #ribbon-bi .bi-chart-subtitle {
      margin: 6px 0 19px;
      font-size: 12px;
      color: #858585;
    }

    #ribbon-bi .bi-canvas-box {
      height: 270px;
      position: relative;
    }

    #ribbon-bi .bi-chart-wide .bi-canvas-box {
      height: 310px;
    }

    #ribbon-bi .bi-empty {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      font-size: 13px;
      color: #8d8d8d;
      text-align: center;
    }

    #ribbon-bi .bi-status {
      margin: 0 0 12px;
      font-size: 13px;
      color: #666;
    }

    @media (max-width: 1050px) {
      #ribbon-bi .bi-kpis {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 760px) {
      #ribbon-bi .bi-charts {
        grid-template-columns: 1fr;
      }

      #ribbon-bi .bi-chart-wide {
        grid-column: auto;
      }
    }

    @media (max-width: 500px) {
      #ribbon-bi .bi-kpis {
        grid-template-columns: 1fr;
      }

      #ribbon-bi .bi-filters,
      #ribbon-bi select,
      #ribbon-bi .bi-refresh {
        width: 100%;
      }
    }
  `;
  document.head.appendChild(style);

  // =====================================================
  // Estrutura do painel
  // =====================================================

  const section = document.createElement("section");
  section.id = "ribbon-bi";

  section.innerHTML = `
    <div class="bi-toolbar">
      <div>
        <h2>Business Intelligence</h2>
        <p>Análise de desempenho, produtividade e qualidade das auditorias</p>
      </div>

      <div class="bi-filters">
        <select id="bi-scope" aria-label="Abrangência">
          <option value="all">Todas as auditorias</option>
          <option value="mine">Minhas auditorias</option>
        </select>

        <select id="bi-days" aria-label="Período">
          <option value="7">Últimos 7 dias</option>
          <option value="30" selected>Últimos 30 dias</option>
          <option value="90">Últimos 90 dias</option>
          <option value="365">Último ano</option>
          <option value="0">Todo o período</option>
        </select>

        <button type="button" class="bi-refresh" id="bi-refresh">
          Atualizar BI
        </button>
      </div>
    </div>

    <p id="bi-status" class="bi-status" role="status"></p>

    <div id="bi-content"></div>
  `;

  const originalKpis = root.querySelector(".kpis");

  if (originalKpis) {
    originalKpis.before(section);
  } else {
    root.appendChild(section);
  }

  // =====================================================
  // Componentes visuais
  // =====================================================

  function kpi(label, value, note, color) {
    return `
      <div class="bi-kpi">
        <div class="bi-kpi-accent"
             style="background:${color}"></div>
        <span class="bi-kpi-label">${escapeHtml(label)}</span>
        <strong class="bi-kpi-value">${escapeHtml(value)}</strong>
        <span class="bi-kpi-note">${escapeHtml(note)}</span>
      </div>
    `;
  }

  function chartCard(id, title, subtitle, wide = false) {
    return `
      <article class="bi-chart-card ${wide ? "bi-chart-wide" : ""}">
        <h3 class="bi-chart-title">${escapeHtml(title)}</h3>
        <p class="bi-chart-subtitle">${escapeHtml(subtitle)}</p>
        <div class="bi-canvas-box">
          <canvas id="${id}"></canvas>
        </div>
      </article>
    `;
  }

  function destroyCharts() {
    for (const chart of charts.values()) {
      chart.destroy();
    }
    charts.clear();
  }

  function baseOptions(extra = {}) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 650,
        easing: "easeOutQuart"
      },
      interaction: {
        mode: "index",
        intersect: false
      },
      plugins: {
        legend: {
          labels: {
            usePointStyle: true,
            pointStyle: "circle",
            padding: 18,
            color: COLORS.text,
            font: { size: 11 }
          }
        },
        tooltip: {
          backgroundColor: "#20252f",
          padding: 12,
          cornerRadius: 7
        }
      },
      ...extra
    };
  }

  function axisOptions() {
    return {
      x: {
        grid: { display: false },
        ticks: {
          color: COLORS.text,
          maxRotation: 0,
          autoSkip: true
        },
        border: { display: false }
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: COLORS.text,
          precision: 0
        },
        grid: {
          color: COLORS.grid
        },
        border: { display: false }
      }
    };
  }

  function createChart(id, config) {
    const canvas = $(id);
    if (!canvas || !window.Chart) return;

    const chart = new Chart(canvas, config);
    charts.set(id, chart);
  }

  function renderCharts(data) {
    const daily = data.audits_by_date || [];
    const profiles = data.audits_by_profile || [];
    const dpi = data.audits_by_dpi || [];
    const categories = data.discrepancies_by_category || [];
    const users = data.audits_by_user || [];
    const s = data.summary || {};

    // 1. Evolução das auditorias
    createChart("bi-chart-trend", {
      type: "line",
      data: {
        labels: daily.map((row) => {
          const [year, month, day] = row.date.split("-");
          return `${day}/${month}`;
        }),
        datasets: [{
          label: "Auditorias",
          data: daily.map((row) => row.count),
          borderColor: COLORS.blue,
          backgroundColor: "rgba(15,98,254,.12)",
          fill: true,
          tension: .35,
          borderWidth: 3,
          pointRadius: 4,
          pointHoverRadius: 7,
          pointBackgroundColor: COLORS.blue
        }]
      },
      options: baseOptions({
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#20252f",
            padding: 12
          }
        },
        scales: axisOptions()
      })
    });

    // 2. Distribuição de resultados
    const unclassified = Math.max(
      0,
      Number(s.processed_pages || 0) -
      Number(s.conforming_pages || 0) -
      Number(s.flagged_pages || 0)
    );

    createChart("bi-chart-status", {
      type: "doughnut",
      data: {
        labels: [
          "Conformes",
          "Divergências reais",
          "Erros de OCR",
          "Pendentes de revisão",
          "Outros status"
        ],
        datasets: [{
          data: [
            s.conforming_pages || 0,
            s.document_discrepancies || 0,
            s.ocr_errors || 0,
            s.pending_review || 0,
            unclassified
          ],
          backgroundColor: [
            COLORS.green,
            COLORS.red,
            COLORS.orange,
            COLORS.purple,
            COLORS.gray
          ],
          borderWidth: 3,
          borderColor: "#fff",
          hoverOffset: 9
        }]
      },
      options: baseOptions({
        cutout: "72%",
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              padding: 15,
              color: COLORS.text
            }
          },
          tooltip: {
            callbacks: {
              label(context) {
                const values = context.dataset.data;
                const total = values.reduce((a, b) => a + Number(b), 0);
                const value = Number(context.raw);
                const share = total ? value / total * 100 : 0;
                return ` ${context.label}: ${number(value)} (${percent(share)})`;
              }
            }
          }
        }
      })
    });

    // 3. Auditorias por DPI
    createChart("bi-chart-dpi", {
      type: "bar",
      data: {
        labels: dpi.map((row) => `${row.dpi} DPI`),
        datasets: [{
          label: "Auditorias",
          data: dpi.map((row) => row.count),
          backgroundColor: [
            COLORS.blue,
            COLORS.cyan,
            COLORS.purple,
            COLORS.teal
          ],
          borderRadius: 7,
          maxBarThickness: 65
        }]
      },
      options: baseOptions({
        plugins: { legend: { display: false } },
        scales: axisOptions()
      })
    });

    // 4. Auditorias por modelo
    createChart("bi-chart-profile", {
      type: "bar",
      data: {
        labels: profiles.map((row) => row.name),
        datasets: [{
          label: "Auditorias",
          data: profiles.map((row) => row.count),
          backgroundColor: COLORS.teal,
          borderRadius: 6,
          maxBarThickness: 42
        }]
      },
      options: baseOptions({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: COLORS.grid }
          },
          y: {
            grid: { display: false }
          }
        }
      })
    });

    // 5. Categorias de divergência
    createChart("bi-chart-categories", {
      type: "polarArea",
      data: {
        labels: categories.map((row) => row.name),
        datasets: [{
          data: categories.map((row) => row.count),
          backgroundColor: palette.map((color) => `${color}bb`),
          borderColor: "#fff",
          borderWidth: 2
        }]
      },
      options: baseOptions({
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              padding: 12
            }
          }
        },
        scales: {
          r: {
            beginAtZero: true,
            ticks: {
              display: false,
              precision: 0
            },
            grid: {
              color: COLORS.grid
            }
          }
        }
      })
    });

    // 6. Ranking por usuário
    if (data.scope === "all") {
      createChart("bi-chart-users", {
        type: "bar",
        data: {
          labels: users.map((row) => row.name),
          datasets: [{
            label: "Auditorias",
            data: users.map((row) => row.count),
            backgroundColor: COLORS.purple,
            borderRadius: 6,
            maxBarThickness: 38
          }]
        },
        options: baseOptions({
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: {
            x: {
              beginAtZero: true,
              ticks: { precision: 0 },
              grid: { color: COLORS.grid }
            },
            y: {
              grid: { display: false }
            }
          }
        })
      });
    }
  }

  function render(data) {
    destroyCharts();

    const s = data.summary || {};

    const cards = [
      kpi("Auditorias realizadas", number(s.audits),
        "No período selecionado", COLORS.blue),

      kpi("Páginas conferidas", number(s.processed_pages),
        "Total processado", COLORS.cyan),

      kpi("Taxa de conformidade", percent(s.conformity_rate),
        "Páginas conformes", COLORS.green),

      kpi("Divergências identificadas", number(s.flagged_pages),
        "Sinalizadas pelo sistema", COLORS.red),

      kpi("Divergências reais", number(s.document_discrepancies),
        "Confirmadas pelo revisor", COLORS.orange),

      kpi("Erros de OCR", number(s.ocr_errors),
        "Classificados pelo revisor", COLORS.purple),

      kpi("Pendentes de revisão", number(s.pending_review),
        "Aguardando parecer", COLORS.red),

      kpi("Guias ausentes", number(s.missing_slips),
        "Sem guia correspondente", COLORS.gray),

      kpi("Tempo médio de OCR", decimal(s.average_ocr_ms, 2) + " ms",
        "Por página medida", COLORS.cyan),

      kpi("Tempo por página", decimal(s.wall_seconds_per_page, 2) + " s",
        "Tempo total / páginas", COLORS.blue),

      kpi("Confiança do código", percent(s.average_code_confidence),
        "Média das leituras", COLORS.teal),

      kpi("Confiança do valor", percent(s.average_amount_confidence),
        "Média das leituras", COLORS.green)
    ].join("");

    const chartDefinitions = [
      chartCard(
        "bi-chart-trend",
        "Evolução das auditorias",
        "Quantidade de auditorias por dia",
        true
      ),
      chartCard(
        "bi-chart-status",
        "Distribuição dos resultados",
        "Conformidade e classificação das divergências"
      ),
      chartCard(
        "bi-chart-dpi",
        "Auditorias por resolução",
        "Distribuição por DPI"
      ),
      chartCard(
        "bi-chart-profile",
        "Auditorias por modelo",
        "Modelos de documento utilizados"
      ),
      chartCard(
        "bi-chart-categories",
        "Categorias de divergência",
        "Tipos de ocorrências detectadas"
      )
    ];

    if (data.scope === "all") {
      chartDefinitions.push(
        chartCard(
          "bi-chart-users",
          "Ranking de usuários",
          "Quantidade de auditorias por usuário",
          true
        )
      );
    }

    $("bi-content").innerHTML = `
      <div class="bi-kpis">${cards}</div>
      <div class="bi-charts">${chartDefinitions.join("")}</div>
    `;

    renderCharts(data);
  }

  // =====================================================
  // Carregamento da biblioteca e da API
  // =====================================================

  let chartLibraryPromise = null;

  function ensureChartJS() {
    if (window.Chart) return Promise.resolve();

    if (!chartLibraryPromise) {
      chartLibraryPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src =
          "https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js";
        script.onload = resolve;
        script.onerror = () => reject(
          new Error("Não foi possível carregar o Chart.js. Verifique sua conexão.")
        );
        document.head.appendChild(script);
      }).catch((error) => {
        chartLibraryPromise = null;
        throw error;
      });
    }

    return chartLibraryPromise;
  }

  let requestId = 0;

  async function loadBI() {
    const id = ++requestId;
    $("bi-status").textContent = "Carregando indicadores e gráficos...";

    const scope = $("bi-scope").value;
    const days = $("bi-days").value;

    try {
      const [response] = await Promise.all([
        fetch(
          `/api/v1/bi/overview?scope=${encodeURIComponent(scope)}&days=${days}`,
          { credentials: "same-origin", cache: "no-store" }
        ),
        ensureChartJS()
      ]);

      if (!response.ok) {
        throw new Error(
          response.status === 401
            ? "Entre na sua conta para consultar o BI."
            : `Erro HTTP ${response.status} ao consultar o BI.`
        );
      }

      const data = await response.json();
      if (id !== requestId) return;

      render(data);
      $("bi-status").textContent = "";
    } catch (error) {
      if (id === requestId) {
        $("bi-status").textContent = error.message;
      }
    }
  }

  $("bi-scope").addEventListener("change", loadBI);
  $("bi-days").addEventListener("change", loadBI);
  $("bi-refresh").addEventListener("click", loadBI);
  $("tab-dashboard")?.addEventListener("click", loadBI);

  const observer = new MutationObserver(() => {
    if (!$("app-content").hidden && !root.hidden) {
      loadBI();
    }
  });

  observer.observe($("app-content"), {
    attributes: true,
    attributeFilter: ["hidden"]
  });

  observer.observe(root, {
    attributes: true,
    attributeFilter: ["hidden"]
  });

  if (!$("app-content").hidden && !root.hidden) {
    loadBI();
  }
})();
