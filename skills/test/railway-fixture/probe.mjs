const [url, serializedOptions] = process.argv.slice(2);
const response = await fetch(url, {
  ...JSON.parse(serializedOptions),
  signal: AbortSignal.timeout(30000),
});
let text = "";
let modelCompletedAtFirstChunk;
for await (const chunk of response.body) {
  if (modelCompletedAtFirstChunk === undefined) {
    const state = await (await fetch("http://127.0.0.1:8081/status")).json();
    modelCompletedAtFirstChunk = state.completed;
    await fetch("http://127.0.0.1:8081/release");
  }
  text += Buffer.from(chunk).toString();
}
console.log(
  JSON.stringify({ status: response.status, text, modelCompletedAtFirstChunk }),
);
