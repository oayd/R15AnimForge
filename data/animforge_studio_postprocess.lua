--[[
	R15 AnimForge — Roblox Studio CurveAnimation Post-Processor (Luau)
	
	Purpose:
	  Roblox Studio's FBX / Curve Editor imports quaternion RotationCurve objects, which fail
	  Marketplace Avatar Item Category validation (which requires EulerRotationCurve order XYZ).
	  This script converts every RotationCurve inside CurveAnimations saved under the target
	  rig's AnimSaves (or a selected Folder / RBX_ANIMSAVES) into valid 60 FPS unwrapped
	  EulerRotationCurves, ensures non-LowerTorso positions are zeroed, creates an empty
	  MarkerCurve + keeps AnimationRigData, sets Loop flags per clip name, and audits banned classes.
	
	Usage (Command Bar or Script in Edit Mode):
	  1. Select your Rig (with AnimSaves ObjectValue) or the CurveAnimation(s) directly, OR leave
	     selection empty to scan ServerStorage.RBX_ANIMSAVES.
	  2. Paste this script into Studio's Command Bar (View -> Command Bar) and press Enter.
	  3. Open Animation Editor on your Rig, load each "... [FIXED]" animation, click "..." ->
	     "Publish to Roblox", and paste the resulting Asset IDs into your pack manifest.
--]]

local Selection = game:GetService("Selection")
local ServerStorage = game:GetService("ServerStorage")

local SAMPLE_FPS = 60
local ALLOWED_CLASSES = {
	MarkerCurve = true,
	AnimationRigData = true,
	Folder = true,
	Vector3Curve = true,
	EulerRotationCurve = true,
	FloatCurve = true,
}

local function unwrapScalar(prev: number, cur: number): number
	local twoPi = 2 * math.pi
	return cur + math.round((prev - cur) / twoPi) * twoPi
end

-- Dual-branch gimbal-safe XYZ Euler unwrapper
local function bestEulerStep(px: number?, py: number?, pz: number?, cf: CFrame): (number, number, number)
	local x, y, z = cf:ToEulerAnglesXYZ()
	if not px or not py or not pz then
		return x, y, z
	end
	local x1, y1, z1 = unwrapScalar(px, x), unwrapScalar(py, y), unwrapScalar(pz, z)
	local d1 = (x1 - px) ^ 2 + (y1 - py) ^ 2 + (z1 - pz) ^ 2

	local x2, y2, z2 = unwrapScalar(px, x + math.pi), unwrapScalar(py, math.pi - y), unwrapScalar(pz, z + math.pi)
	local d2 = (x2 - px) ^ 2 + (y2 - py) ^ 2 + (z2 - pz) ^ 2

	if d2 < d1 then
		return x2, y2, z2
	end
	return x1, y1, z1
end

local function inferLoopFlag(clipName: string, currentLoop: boolean): boolean
	local low = string.lower(clipName)
	if string.find(low, "jump", 1, true) then
		return false
	end
	if string.find(low, "idle", 1, true)
		or string.find(low, "walk", 1, true)
		or string.find(low, "run", 1, true)
		or string.find(low, "fall", 1, true)
		or string.find(low, "climb", 1, true)
		or string.find(low, "swim", 1, true)
	then
		return true
	end
	return currentLoop
end

local function getCurveMaxTime(ca: CurveAnimation): number
	local maxT = 0
	for _, d in ipairs(ca:GetDescendants()) do
		if d:IsA("FloatCurve") or d:IsA("RotationCurve") then
			local ks = (d :: any):GetKeys()
			if #ks > 0 then
				maxT = math.max(maxT, ks[#ks].Time)
			end
		end
	end
	return maxT
end

local function convertCurveAnimation(srcCA: CurveAnimation, parentFolder: Instance): (CurveAnimation, number, number)
	local cleanBaseName = srcCA.Name:gsub("%s*%[FIXED%]$", "")
	local outName = cleanBaseName .. " [FIXED]"
	local oldOut = parentFolder:FindFirstChild(outName)
	if oldOut then
		oldOut:Destroy()
	end

	local maxT = getCurveMaxTime(srcCA)
	if maxT <= 0 then
		error("CurveAnimation '" .. srcCA.Name .. "' has duration 0")
	end

	local nSteps = math.max(2, math.floor(maxT * SAMPLE_FPS + 0.5))
	local dt = maxT / nSteps

	local newCA = Instance.new("CurveAnimation")
	newCA.Name = outName
	newCA.Loop = inferLoopFlag(cleanBaseName, srcCA.Loop)
	newCA.Priority = srcCA.Priority

	local rigData = srcCA:FindFirstChildWhichIsA("AnimationRigData")
	if rigData then
		rigData:Clone().Parent = newCA
	end

	local mc = Instance.new("MarkerCurve")
	mc.Name = "NamedKeyframeEvent"
	mc.Parent = newCA

	local maxErrDeg = 0

	local function convertFolder(srcFolder: Folder, parentInst: Instance)
		local newFolder = Instance.new("Folder")
		newFolder.Name = srcFolder.Name
		newFolder.Parent = parentInst

		local srcPos = srcFolder:FindFirstChild("Position")
		if srcPos and srcPos:IsA("Vector3Curve") then
			local newPos = Instance.new("Vector3Curve")
			newPos.Name = "Position"
			local px = Instance.new("FloatCurve")
			px.Name = "X"
			px.Parent = newPos
			local py = Instance.new("FloatCurve")
			py.Name = "Y"
			py.Parent = newPos
			local pz = Instance.new("FloatCurve")
			pz.Name = "Z"
			pz.Parent = newPos

			if srcFolder.Name == "LowerTorso" then
				local kx, ky, kz = {}, {}, {}
				for i = 0, nSteps do
					local tOut = if i == nSteps then maxT else i * dt
					local pv = srcPos:GetValueAtTime(tOut)
					table.insert(kx, FloatCurveKey.new(tOut, pv[1] or 0, Enum.KeyInterpolationMode.Linear))
					table.insert(ky, FloatCurveKey.new(tOut, pv[2] or 0, Enum.KeyInterpolationMode.Linear))
					table.insert(kz, FloatCurveKey.new(tOut, pv[3] or 0, Enum.KeyInterpolationMode.Linear))
				end
				px:SetKeys(kx)
				py:SetKeys(ky)
				pz:SetKeys(kz)
			else
				local zeroKeys = {
					FloatCurveKey.new(0, 0, Enum.KeyInterpolationMode.Linear),
					FloatCurveKey.new(maxT, 0, Enum.KeyInterpolationMode.Linear),
				}
				px:SetKeys(zeroKeys)
				py:SetKeys(zeroKeys)
				pz:SetKeys(zeroKeys)
			end
			newPos.Parent = newFolder
		end

		local srcRot = srcFolder:FindFirstChild("Rotation")
		if srcRot then
			local erc = Instance.new("EulerRotationCurve")
			erc.Name = "Rotation"
			erc.RotationOrder = Enum.RotationOrder.XYZ
			local fx = Instance.new("FloatCurve")
			fx.Name = "X"
			fx.Parent = erc
			local fy = Instance.new("FloatCurve")
			fy.Name = "Y"
			fy.Parent = erc
			local fz = Instance.new("FloatCurve")
			fz.Name = "Z"
			fz.Parent = erc

			local kx, ky, kz = {}, {}, {}
			local px, py, pz = nil, nil, nil
			for i = 0, nSteps do
				local tOut = if i == nSteps then maxT else i * dt
				local cf = if srcRot:IsA("EulerRotationCurve")
					then (srcRot:GetRotationAtTime(tOut) or CFrame.identity)
					elseif srcRot:IsA("RotationCurve") then (srcRot:GetValueAtTime(tOut) or CFrame.identity)
					else CFrame.identity
				local ex, ey, ez = bestEulerStep(px, py, pz, cf)
				px, py, pz = ex, ey, ez
				table.insert(kx, FloatCurveKey.new(tOut, ex, Enum.KeyInterpolationMode.Linear))
				table.insert(ky, FloatCurveKey.new(tOut, ey, Enum.KeyInterpolationMode.Linear))
				table.insert(kz, FloatCurveKey.new(tOut, ez, Enum.KeyInterpolationMode.Linear))
			end
			fx:SetKeys(kx)
			fy:SetKeys(ky)
			fz:SetKeys(kz)
			erc.Parent = newFolder

			for i = 0, nSteps - 1 do
				local tMid = (i + 0.5) * dt
				local c1 = if srcRot:IsA("EulerRotationCurve")
					then (srcRot:GetRotationAtTime(tMid) or CFrame.identity)
					elseif srcRot:IsA("RotationCurve") then (srcRot:GetValueAtTime(tMid) or CFrame.identity)
					else CFrame.identity
				local c2 = erc:GetRotationAtTime(tMid)
				maxErrDeg = math.max(maxErrDeg, math.deg(math.abs(c1:AngleBetween(c2))))
			end
		end

		for _, child in ipairs(srcFolder:GetChildren()) do
			if child:IsA("Folder") then
				convertFolder(child, newFolder)
			end
		end
	end

	local hrpFolder = srcCA:FindFirstChild("HumanoidRootPart")
	if hrpFolder and hrpFolder:IsA("Folder") then
		convertFolder(hrpFolder, newCA)
	end

	local bannedCount = 0
	for _, d in ipairs(newCA:GetDescendants()) do
		if not ALLOWED_CLASSES[d.ClassName] then
			bannedCount += 1
		end
	end

	newCA.Parent = parentFolder
	return newCA, bannedCount, maxErrDeg
end

local function collectTargets(): { CurveAnimation }
	local targets: { CurveAnimation } = {}
	local sel = Selection:Get()
	for _, item in ipairs(sel) do
		if item:IsA("CurveAnimation") then
			table.insert(targets, item)
		else
			local savesVal = item:FindFirstChild("AnimSaves")
			local root = if savesVal and savesVal:IsA("ObjectValue") and savesVal.Value then savesVal.Value else item
			for _, d in ipairs(root:GetDescendants()) do
				if d:IsA("CurveAnimation") and not d.Name:find("%[FIXED%]$") then
					table.insert(targets, d)
				end
			end
		end
	end
	if #targets == 0 then
		local rbxSaves = ServerStorage:FindFirstChild("RBX_ANIMSAVES")
		if rbxSaves then
			for _, d in ipairs(rbxSaves:GetDescendants()) do
				if d:IsA("CurveAnimation") and not d.Name:find("%[FIXED%]$") then
					table.insert(targets, d)
				end
			end
		end
	end
	return targets
end

local targets = collectTargets()
print(string.format("[AnimForge PostProcess] Found %d source CurveAnimation(s)", #targets))
for _, ca in ipairs(targets) do
	local parent = ca.Parent
	if parent then
		local ok, newCA, banned, errDeg = pcall(convertCurveAnimation, ca, parent)
		if ok then
			print(string.format("  OK %-36s Loop=%-5s banned=%d maxErr=%.2f deg", newCA.Name, tostring(newCA.Loop), banned, errDeg))
		else
			warn("  FAIL " .. ca.Name .. ": " .. tostring(newCA))
		end
	end
end
print("[AnimForge PostProcess] Complete. Publish the [FIXED] clips via Animation Editor -> Publish to Roblox.")
