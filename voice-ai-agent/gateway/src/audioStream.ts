export class AudioStreamHandler {
    private chunks: Buffer[] = [];

    constructor() {}

    /**
     * Appends a new raw binary audio chunk to the session buffer.
     */
    public append(chunk: Buffer): void {
        this.chunks.push(chunk);
    }

    /**
     * Combines all accumulated binary chunks into a single unified Buffer.
     */
    public getBuffer(): Buffer {
        return Buffer.concat(this.chunks);
    }

    /**
     * Clears all accumulated buffers to free memory.
     */
    public clear(): void {
        this.chunks = [];
    }

    /**
     * Gets the total size in bytes of the accumulated buffer.
     */
    public get size(): number {
        return this.chunks.reduce((acc, val) => acc + val.length, 0);
    }
}
