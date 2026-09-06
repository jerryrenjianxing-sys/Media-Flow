/** Refresh independent panels immediately; keep successful and prior data. */
export async function loadIndependentSections(sections) {
  const outcomes = await Promise.all(sections.map(async section => {
    try {
      await section.run();
      return null;
    } catch (error) {
      const reason = error instanceof Error && !(error instanceof TypeError)
        ? error.message : '连接暂不可用，请检查连接后重新读取';
      return `${section.label}：${reason}`;
    }
  }));
  return outcomes.filter(value => value !== null);
}
