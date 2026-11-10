Texture2D<float4> t : register(t0); SamplerState s : register(s0); float4 main(float4 p : SV_Position) : SV_Target0 { float2 uv=p.xy/float2(2,1); return t.SampleLevel(s,uv,p.x<1?1:0); }
