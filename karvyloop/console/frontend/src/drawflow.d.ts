/* 最小类型声明:drawflow(npm,纯 JS,无内置 .d.ts)。只声明我们用到的 API。 */
declare module "drawflow" {
  export default class Drawflow {
    constructor(container: HTMLElement);
    reroute: boolean;
    editor_mode: string;
    start(): void;
    import(data: unknown): void;
    export(): Record<string, unknown>;
    addNode(
      name: string, inputs: number, outputs: number, posx: number, posy: number,
      className: string, data: Record<string, unknown>, html: string, typenode?: boolean
    ): number;
    clear(): void;
    on(event: string, callback: (...args: unknown[]) => void): void;
    zoom_in(): void;
    zoom_out(): void;
    zoom_reset(): void;
  }
}
