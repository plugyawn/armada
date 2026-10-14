// @ts-check

/** @type {Parameters<typeof import("babel-plugin-formatjs").default>[1]} */
const formatJsOptions = {
    additionalFunctionNames: ["$t", "$t_html"],
    overrideIdFn: (_id, defaultMessage) => defaultMessage ?? "",
};

/** @type {import("@babel/preset-env").Options} */
const presetEnvOptions = {
    shippedProposals: true,
    useBuiltIns: false,
};

/** @type {import("@babel/core").TransformOptions} */
const config = {
    plugins: [
        ["formatjs", formatJsOptions],
        // .tsx under web/src/armada/ compiles to Preact's automatic runtime.
        ["@babel/plugin-transform-react-jsx", {runtime: "automatic", importSource: "preact"}],
    ],
    presets: [["@babel/preset-env", presetEnvOptions], "@babel/typescript"],
};
export default config;
