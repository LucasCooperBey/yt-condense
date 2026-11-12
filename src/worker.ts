import Anthropic from "@anthropic-ai/sdk";

interface Env {
  CLAUDE_API_KEY: string;
  API_SECRET: string;
  ELEVENLABS_API_KEY?: string;
  MODEL?: string;
}

interface RequestBody {
  transcript?: string;
  videoId?: string;
  title?: string;
  url?: string;
  fromTimestamp?: number;
  length?: number;
  outputFormat?: "text" | "audio";
}

const DEFAULT_MODEL = "claude-sonnet-5-5";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return corsResponse(null);
    }

    const authHeader = request.headers.get("Authorization");
    if (authHeader !== `Bearer ${env.API_SECRET}`) {
      return corsResponse("Unauthorized", 401);
    }

    if (request.method !== "POST") {
      return corsResponse("Method not allowed", 405);
    }

    try {
      const body = (await request.json()) as RequestBody;
      const transcript = body.transcript ?? "";
      const fromTimestamp =
        typeof body.fromTimestamp === "number" && Number.isFinite(body.fromTimestamp)
          ? body.fromTimestamp
          : 0;
      const length =
        typeof body.length === "number" && Number.isFinite(body.length) && body.length > 0
          ? body.length
          : 20;
      const videoId = body.videoId || "unknown";
      const title = body.title || videoId;
      const url = body.url || (body.videoId ? `https://www.youtube.com/watch?v=${body.videoId}` : "unknown");
      const outputFormat = body.outputFormat === "audio" ? "audio" : "text";

      if (transcript.length < 100) {
        return corsResponse("Transcript too short (min 100 chars)", 400);
      }

      if (transcript.length > 100000) {
        return corsResponse(
          "Transcript is longer than 100,000 characters. Use the command-line tool for longer videos.",
          400,
        );
      }

      const client = new Anthropic({ apiKey: env.CLAUDE_API_KEY });
      const message = await client.messages.create({
        model: env.MODEL || DEFAULT_MODEL,
        max_tokens: 4000,
        messages: [
          {
            role: "user",
            content: buildPrompt(transcript, title, url, fromTimestamp, length),
          },
        ],
      });

      let condensed = "";
      for (const block of message.content) {
        if (block.type === "text") {
          condensed += block.text;
        }
      }
      if (!condensed) {
        return corsResponse("The model returned no text.", 502);
      }

      if (outputFormat !== "audio" || !env.ELEVENLABS_API_KEY) {
        return corsResponse(condensed);
      }

      const voiceId = "21m00Tcm4TlvDq8ikWAM";
      const ttsResponse = await fetch(
        `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "xi-api-key": env.ELEVENLABS_API_KEY,
          },
          body: JSON.stringify({
            text: condensed,
            model_id: "eleven_turbo_v2_5",
            voice_settings: {
              stability: 0.5,
              similarity_boost: 0.75,
              speed: 1.1,
            },
          }),
        },
      );

      if (!ttsResponse.ok) {
        const error = await ttsResponse.text();
        console.error("ElevenLabs error:", error);
        return corsResponse(condensed);
      }

      const audioBuffer = await ttsResponse.arrayBuffer();
      const audioBase64 = bytesToBase64(new Uint8Array(audioBuffer));

      return jsonResponse({
        text: condensed,
        audio: audioBase64,
        duration: Math.round(condensed.length / 15),
      });
    } catch (error) {
      console.error("Error processing request:", error);
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      return corsResponse(`Processing failed: ${errorMessage}`, 500);
    }
  },
};

function buildPrompt(
  transcript: string,
  title: string,
  url: string,
  fromSeconds: number,
  length: number,
): string {
  return `Transform this YouTube transcript into a condensed audio summary.

RULES:
- Flowing conversational prose (NO bullets, NO lists, NO markdown)
- Short sentences suitable for text-to-speech
- Remove ALL filler: "um", "like", "you know", "basically", "so"
- Remove ALL meta-content: intros, outros, "subscribe", sponsors, "in this video"
- Remove ALL repetition: if a point was made, don't restate it
- Keep ONLY: novel insights, specific facts, actionable advice, concrete examples
- Target ~${length}% of original length
- Start IMMEDIATELY with content (no preamble)

VIDEO: ${title} (${url})
FROM TIMESTAMP: ${fromSeconds} seconds

TRANSCRIPT:
${transcript}`;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let index = 0; index < bytes.length; index += step) {
    const slice = bytes.subarray(index, index + step);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

function corsResponse(body: string | null, status = 200): Response {
  return new Response(body, {
    status,
    headers: corsHeaders("text/plain; charset=utf-8"),
  });
}

function jsonResponse(body: { text: string; audio: string; duration: number }): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: corsHeaders("application/json"),
  });
}

function corsHeaders(contentType: string): HeadersInit {
  return {
    "Content-Type": contentType,
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
}
