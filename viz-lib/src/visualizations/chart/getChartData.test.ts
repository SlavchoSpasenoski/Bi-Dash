import getChartData from "./getChartData";

describe("Visualizations", () => {
  describe("Chart", () => {
    describe("getChartData", () => {
      test("Single series", () => {
        const { input, output } = require("./fixtures/getChartData/single-series");
        const data = getChartData(input.data, input.options);
        expect(data).toEqual(output.data);
      });

      test("Multiple series: multiple Y mappings", () => {
        const { input, output } = require("./fixtures/getChartData/multiple-series-multiple-y");
        const data = getChartData(input.data, input.options);
        expect(data).toEqual(output.data);
      });

      test("Multiple series: grouped", () => {
        const { input, output } = require("./fixtures/getChartData/multiple-series-grouped");
        const data = getChartData(input.data, input.options);
        expect(data).toEqual(output.data);
      });

      test("Dataset chart preserves distinct combinations of grouping fields", () => {
        const rows = [
          { region: "West", channel: "Online", total: 15 },
          { region: "West", channel: "Store", total: 20 },
        ];
        const [series] = getChartData(rows, {
          columnMapping: { region: "x", total: "y" },
          datasetGrouping: ["region", "channel"],
          seriesOptions: {},
        }) as Array<{ data: Array<{ x: string; y: number }> }>;
        expect(series.data.map(({ x, y }) => [JSON.parse(x), y])).toEqual([
          [["West", "Online"], 15],
          [["West", "Store"], 20],
        ]);
      });

      test("Multiple series: sorted", () => {
        const { input, output } = require("./fixtures/getChartData/multiple-series-sorted");
        const data = getChartData(input.data, input.options);
        expect(data).toEqual(output.data);
      });
    });
  });
});
