(async function init() {
  const user = await distill.bindAuthChrome();
  if (!user) return;

  distillMeetings.bindNewMeetingButtons({ loginNext: "/dashboard" });
  await distillMeetings.loadMeetings({ loginNext: "/dashboard" });
})();
