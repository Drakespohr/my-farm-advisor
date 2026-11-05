// Runs fieldmonitor/static/crossroads.js in Node with a fake Overpass; prints JSON results.
const fs = require("fs"), path = require("path");
global.window = {};
const grid = JSON.parse(process.argv[2]);
global.fetch = async (url, opts) => ({ ok: true, status: 200, json: async () => grid });
eval(fs.readFileSync(path.join(__dirname, "..", "fieldmonitor", "static", "crossroads.js"), "utf8"));
(async () => {
  const X = window.OveracreCrossroads;
  const out = {
    exact: await X.find("250th and Waverly Road", [40.9, -96.5]),
    misspaced: await X.find("250TH & waverly rd", [40.9, -96.5]),
    tee: await X.find("148th and waverly", [40.9, -96.55]),
    notCross: X.parse("North 80"),
    regex2500: new RegExp(X.nameRegex("250th"), "i").test("North 2500th Street"),
  };
  console.log(JSON.stringify(out));
})();
