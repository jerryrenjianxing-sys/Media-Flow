export function loadIndependentSections(sections: ReadonlyArray<{
  label: string;
  run: () => Promise<void>;
}>): Promise<string[]>;
