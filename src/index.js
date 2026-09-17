const API_URL =
  "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json";

export default {
  async fetch(request, env) {
    return new Response("WinGo 1M Collector is running");
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(collectResults(env));
  }
};

async function collectResults(env) {
  try {
    const url = `${API_URL}?ts=${Date.now()}&pageSize=50`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://draw.ar-lottery01.com/"
      }
    });

    const text = await response.text();

    if (!response.ok) {
      console.log(`WinGo API HTTP ${response.status}`);
      console.log(text.slice(0, 500));
      return;
    }

    let data;

    try {
      data = JSON.parse(text);
    } catch (e) {
      console.log("WinGo API returned invalid JSON");
      return;
    }

    const list =
      data?.data?.list ??
      data?.data?.records ??
      data?.list ??
      data?.records ??
      [];

    if (!Array.isArray(list) || list.length === 0) {
      console.log("No WinGo 1M records found");
      return;
    }

    const rows = [];

    for (const item of list) {
      const issue = String(
        item.issueNumber ??
        item.issue ??
        item.period ??
        item.id ??
        ""
      ).trim();

      const rawNumber =
        item.number ??
        item.openNumber ??
        item.code ??
        item.result;

      const number = Number(rawNumber);

      if (
        !issue ||
        !Number.isInteger(number) ||
        number < 0 ||
        number > 9
      ) {
        continue;
      }

      rows.push({
        issue: issue,
        number: number,
        big_small: number >= 5 ? "Big" : "Small",
        color: getColor(item, number)
      });
    }

    if (rows.length === 0) {
      console.log("No valid rows after parsing");
      return;
    }

    const supabaseUrl = env.SUPABASE_URL;
    const supabaseKey = env.SUPABASE_KEY;

    if (!supabaseUrl || !supabaseKey) {
      console.log("SUPABASE_URL or SUPABASE_KEY is missing");
      return;
    }

    const supabaseResponse = await fetch(
      `${supabaseUrl}/rest/v1/results`,
      {
        method: "POST",
        headers: {
          "apikey": supabaseKey,
          "Authorization": `Bearer ${supabaseKey}`,
          "Content-Type": "application/json",
          "Prefer": "resolution=merge-duplicates,return=minimal"
        },
        body: JSON.stringify(rows)
      }
    );

    const supabaseText = await supabaseResponse.text();

    console.log(
      `WinGo 1M: parsed=${rows.length} Supabase=${supabaseResponse.status}`
    );

    if (!supabaseResponse.ok) {
      console.log(supabaseText.slice(0, 500));
    }
  } catch (error) {
    console.log("Collector error:", String(error));
  }
}

function getColor(item, number) {
  const apiColor =
    item.color ??
    item.colour ??
    item.colorName ??
    item.colourName;

  if (apiColor !== undefined && apiColor !== null) {
    return String(apiColor);
  }

  // Standard WinGo number-color mapping
  if (number === 5) return "Violet";
  if ([0, 2, 4, 6, 8].includes(number)) return "Red";
  if ([1, 3, 7, 9].includes(number)) return "Green";

  return "";
}
