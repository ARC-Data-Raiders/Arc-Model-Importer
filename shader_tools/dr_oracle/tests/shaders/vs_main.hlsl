float4 main(uint id : SV_VertexID) : SV_Position { float4 p[3] = { float4(-1,-1,0,1), float4(-1,3,0,1), float4(3,-1,0,1) }; return p[id]; }
