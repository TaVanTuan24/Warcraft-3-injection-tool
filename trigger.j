globals
    trigger gg_trg_TeleportToHero= null
    trigger gg_trg_FullVisibility= null
    unit udh_LastConvertedUnit= null
    group udh_TempGroup= null
    unit udh_TempHero= null
    unit udh_SelectedUnit= null
    integer udh_UnitType= 0
    location udh_Position= null
    real udh_CameraZoomt= 0
    trigger gg_trg_ConvertUnit= null
    trigger gg_trg_TakeControlSimple= null
    trigger gg_trg_CameraZoomt= null
    integer udh_ReviveChance = 20
endglobals

function Trig_FullVisibility_Actions takes nothing returns nothing
    call CreateFogModifierRectBJ(true, Player(0), FOG_OF_WAR_VISIBLE, GetEntireMapRect())
endfunction

function InitTrig_FullVisibility takes nothing returns nothing
    set gg_trg_FullVisibility=CreateTrigger()
    call TriggerAddAction(gg_trg_FullVisibility, function Trig_FullVisibility_Actions)
endfunction

function Trig_SetReviveChance_Actions takes nothing returns nothing
    local player p = GetTriggerPlayer()
    local integer id = GetPlayerId(p)
    local string msg = GetEventPlayerChatString()
    local string val = SubString(msg, 5, StringLength(msg)) // lấy phần sau "-rev "
    local integer chance = S2I(val)
    if id == 0 then // chỉ player 1 (red)
        if chance >= 0 and chance <= 100 then
            set udh_ReviveChance = chance
            call DisplayTimedTextToPlayer(p, 0, 0, 10, "Tỉ lệ hồi sinh hiện tại: " + I2S(chance) + "%")
        else
            call DisplayTimedTextToPlayer(p, 0, 0, 10, "Vui lòng nhập giá trị từ 0 đến 100.")
        endif
    endif

    set p = null
endfunction

function InitTrig_SetReviveChance takes nothing returns nothing
    local trigger t = CreateTrigger()
    call TriggerRegisterPlayerChatEvent(t, Player(0), "-rev", false)
    call TriggerAddAction(t, function Trig_SetReviveChance_Actions)
endfunction
function Trig_ConvertUnit_Conditions takes nothing returns boolean
    if ( not ( IsUnitType(GetTriggerUnit(), UNIT_TYPE_STRUCTURE) == false ) ) then
        return false
    endif
    if ( not ( IsUnitType(GetTriggerUnit(), UNIT_TYPE_HERO) == false ) ) then
        return false
    endif
    if ( not ( GetOwningPlayer(GetKillingUnitBJ()) == Player(0) ) ) then
        return false
    endif
    return true
endfunction

function Trig_ConvertUnit_Func002C takes nothing returns boolean
    if ( not ( GetRandomInt(0, 100) <= udh_ReviveChance ) ) then
        return false
    endif
    return true
endfunction

function Trig_ConvertUnit_Actions takes nothing returns nothing
    if ( Trig_ConvertUnit_Func002C() ) then
        set udh_Position=GetUnitLoc(GetTriggerUnit())
        set udh_UnitType=GetUnitTypeId(GetTriggerUnit())
        call CreateNUnitsAtLoc(1, udh_UnitType, Player(0), udh_Position, bj_UNIT_FACING)
        set udh_LastConvertedUnit = GetLastCreatedUnit()
        call SetUnitLifePercentBJ(GetLastCreatedUnit(), 100)
        call AddSpecialEffectLocBJ(udh_Position, "Abilities\\Spells\\Human\\Resurrect\\ResurrectTarget.mdl")
        call RemoveLocation(udh_Position)
    else
    endif
endfunction

function InitTrig_ConvertUnit takes nothing returns nothing
    set gg_trg_ConvertUnit=CreateTrigger()
    call TriggerRegisterAnyUnitEventBJ(gg_trg_ConvertUnit, EVENT_PLAYER_UNIT_DEATH)
    call TriggerAddCondition(gg_trg_ConvertUnit, Condition(function Trig_ConvertUnit_Conditions))
    call TriggerAddAction(gg_trg_ConvertUnit, function Trig_ConvertUnit_Actions)
endfunction

function Trig_TeleportToHero_Func002002001002 takes nothing returns boolean
    return ( IsUnitType(GetFilterUnit(), UNIT_TYPE_HERO) == true )
endfunction

function Trig_TeleportToHero_Func003Func001C takes nothing returns boolean
    if ( not ( GetOwningPlayer(GetEnumUnit()) == Player(0) ) ) then
        return false
    endif
    if ( not ( udh_TempHero != null ) ) then
        return false
    endif
    return true
endfunction

function Trig_TeleportToHero_Func003A takes nothing returns nothing
    if ( Trig_TeleportToHero_Func003Func001C() ) then
        call SetUnitPositionLoc(GetEnumUnit(), GetUnitLoc(udh_TempHero))
    else
        call DoNothing()
    endif
endfunction

function Trig_TeleportToHero_Actions takes nothing returns nothing
    set udh_TempGroup=GetUnitsSelectedAll(Player(0))
    set udh_TempHero=GroupPickRandomUnit(GetUnitsOfPlayerMatching(Player(0), Condition(function Trig_TeleportToHero_Func002002001002)))
    call ForGroupBJ(udh_TempGroup, function Trig_TeleportToHero_Func003A)
    call DestroyGroup(udh_TempGroup)
endfunction

function InitTrig_TeleportToHero takes nothing returns nothing
    set gg_trg_TeleportToHero=CreateTrigger()
    call TriggerRegisterPlayerKeyEventBJ(gg_trg_TeleportToHero, Player(0), bj_KEYEVENTTYPE_DEPRESS, bj_KEYEVENTKEY_RIGHT)
    call TriggerAddAction(gg_trg_TeleportToHero, function Trig_TeleportToHero_Actions)
endfunction

function Trig_TakeControlSimple_Func002C takes nothing returns boolean
    if ( not ( CountUnitsInGroup(GetUnitsSelectedAll(Player(0))) == 1 ) ) then
        return false
    endif
    return true
endfunction

function Trig_TakeControlSimple_Actions takes nothing returns nothing
    set udh_SelectedUnit=GroupPickRandomUnit(GetUnitsSelectedAll(Player(0)))
    if ( Trig_TakeControlSimple_Func002C() ) then
        call SetUnitOwner(udh_SelectedUnit, Player(0), true)
    else
    endif
    call DisplayTextToForce(GetPlayersAll(), "TRIGSTR_001")
endfunction

function InitTrig_TakeControlSimple takes nothing returns nothing
    set gg_trg_TakeControlSimple=CreateTrigger()
    call TriggerRegisterPlayerKeyEventBJ(gg_trg_TakeControlSimple, Player(0), bj_KEYEVENTTYPE_DEPRESS, bj_KEYEVENTKEY_UP)
    call TriggerAddAction(gg_trg_TakeControlSimple, function Trig_TakeControlSimple_Actions)
endfunction

function Trig_StoreSelectedUnit_Actions takes nothing returns nothing
    set udh_SelectedUnit = GetTriggerUnit()
endfunction

function Filter_IsHeroOfPlayer1 takes nothing returns boolean
    return (IsUnitType(GetFilterUnit(), UNIT_TYPE_HERO) and GetOwningPlayer(GetFilterUnit()) == Player(0))
endfunction

function Trig_TeleportToSelectedUnit_Actions takes nothing returns nothing
    local group g
    local unit randomHero
    local real x
    local real y
    local effect fx
    if udh_SelectedUnit == null then
        return
    endif
    set g = GetUnitsOfPlayerMatching(Player(0), Condition(function Filter_IsHeroOfPlayer1))
    set randomHero = GroupPickRandomUnit(g)
    if randomHero != null then
        set x = GetUnitX(udh_SelectedUnit)
        set y = GetUnitY(udh_SelectedUnit)
        call SetUnitPosition(randomHero, x, y)
        set fx = AddSpecialEffect("Abilities\\Spells\\Human\\MassTeleport\\MassTeleportTarget.mdl", x, y)
        call DestroyEffect(fx)
    endif
    call DestroyGroup(g)
    set randomHero = null
    set fx = null
endfunction

function InitTrig_TeleportToSelectedUnit takes nothing returns nothing
    local trigger t = CreateTrigger()
    call TriggerRegisterPlayerKeyEventBJ(t, Player(0), bj_KEYEVENTTYPE_DEPRESS, bj_KEYEVENTKEY_LEFT)
    call TriggerAddAction(t, function Trig_TeleportToSelectedUnit_Actions)
endfunction

function InitTrig_StoreSelectedUnit takes nothing returns nothing
    local trigger t = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(t, Player(0), EVENT_PLAYER_UNIT_SELECTED, null)
    call TriggerAddAction(t, function Trig_StoreSelectedUnit_Actions)
endfunction

function Trig_ShowCoords_Actions takes nothing returns nothing
    local location loc
    set loc = GetUnitLoc(udh_SelectedUnit)
    call DisplayTextToPlayer(GetLocalPlayer(), 0, 0, "Tọa độ: X=" + R2S(GetLocationX(loc)) + ", Y=" + R2S(GetLocationY(loc)))
    call RemoveLocation(loc)
    set loc = null
endfunction

function InitTrig_ShowCoords takes nothing returns nothing
    local trigger t = CreateTrigger()
    call TriggerRegisterPlayerKeyEventBJ(t, Player(0), bj_KEYEVENTTYPE_DEPRESS, bj_KEYEVENTKEY_DOWN)
    call TriggerAddAction(t, function Trig_ShowCoords_Actions)
endfunction

function main takes nothing returns nothing
    call InitTrig_ConvertUnit()
    call InitTrig_TakeControlSimple()
    call InitTrig_TeleportToHero()
    call InitTrig_TeleportToSelectedUnit()
    call InitTrig_StoreSelectedUnit()
    call InitTrig_ShowCoords()
    call InitTrig_SetReviveChance()

    call InitTrig_FullVisibility()
    call ConditionalTriggerExecute(gg_trg_FullVisibility)
endfunction